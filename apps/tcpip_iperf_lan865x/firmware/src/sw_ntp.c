/*
 * sw_ntp.c — software NTP client/server using SW timestamps for T1..T4.
 * See sw_ntp.h for protocol/design overview.
 */

#include "sw_ntp.h"
#include "sw_ntp_offset_trace.h"
#include "ptp_clock.h"

#include <string.h>

#include "system/time/sys_time.h"
#include "system/console/sys_console.h"

#define TCPIP_THIS_MODULE_ID    TCPIP_MODULE_MANAGER
#include "library/tcpip/tcpip.h"
#include "library/tcpip/udp.h"

/* -------------------------------------------------------------------------
 * Protocol constants
 * ---------------------------------------------------------------------- */

#define SW_NTP_UDP_PORT         12345u
#define SW_NTP_DEFAULT_POLL_MS  1000u
#define SW_NTP_RESP_TIMEOUT_MS  200u

#define SW_NTP_PKT_REQ          1u
#define SW_NTP_PKT_RESP         2u

#pragma pack(push, 1)
typedef struct {
    uint8_t  type;           /* SW_NTP_PKT_REQ or SW_NTP_PKT_RESP */
    uint8_t  seq;            /* follower sequence number         */
    uint8_t  reserved[6];    /* keep 8-byte alignment for T-values */
    int64_t  t1_ns;          /* follower send time               */
    int64_t  t2_ns;          /* master recv time                 */
    int64_t  t3_ns;          /* master send time                 */
} sw_ntp_pkt_t;
#pragma pack(pop)

/* -------------------------------------------------------------------------
 * Module state
 * ---------------------------------------------------------------------- */

static sw_ntp_mode_t s_mode          = SW_NTP_OFF;
static UDP_SOCKET    s_sock          = INVALID_UDP_SOCKET;

static uint32_t      s_master_ip_host   = 0u;                     /* follower only */
static uint32_t      s_poll_interval_ms = SW_NTP_DEFAULT_POLL_MS;
static bool          s_verbose          = false;

/* Follower */
static uint8_t  s_fol_seq            = 0u;
static uint8_t  s_fol_pending_seq    = 0u;
static bool     s_fol_request_outstanding = false;
static uint64_t s_fol_last_tx_tick   = 0u;
static uint64_t s_fol_pending_deadline_tick = 0u;
static uint32_t s_fol_samples        = 0u;
static uint32_t s_fol_timeouts       = 0u;
static int64_t  s_fol_last_offset_ns = 0;

/* Master */
static uint32_t s_gm_responses       = 0u;

/* -------------------------------------------------------------------------
 * Internal helpers
 * ---------------------------------------------------------------------- */

static uint64_t ticks_per_ms_cached(void)
{
    static uint64_t ticks_per_ms = 0u;
    if (ticks_per_ms == 0u) {
        ticks_per_ms = (uint64_t)SYS_TIME_FrequencyGet() / 1000ULL;
    }
    return ticks_per_ms;
}

static void sw_ntp_close_socket(void)
{
    if (s_sock != INVALID_UDP_SOCKET) {
        (void)TCPIP_UDP_Close(s_sock);
        s_sock = INVALID_UDP_SOCKET;
    }
}

static bool sw_ntp_stack_is_up(void)
{
    TCPIP_NET_HANDLE net = TCPIP_STACK_IndexToNet(0);
    if (net == NULL) {
        return false;
    }
    return TCPIP_STACK_NetIsUp(net);
}

static bool sw_ntp_open_master(void)
{
    s_sock = TCPIP_UDP_ServerOpen(IP_ADDRESS_TYPE_IPV4, SW_NTP_UDP_PORT, NULL);
    if (s_sock == INVALID_UDP_SOCKET) {
        return false;
    }
    SYS_CONSOLE_PRINT("[SW-NTP] master listening on UDP port %u\r\n",
                      (unsigned)SW_NTP_UDP_PORT);
    return true;
}

static bool sw_ntp_open_follower(void)
{
    if (s_master_ip_host == 0u) {
        SYS_CONSOLE_PRINT("[SW-NTP] follower: no master IP set\r\n");
        return false;
    }
    IP_MULTI_ADDRESS remote;
    memset(&remote, 0, sizeof(remote));
    /* Harmony stores v4 addresses LITTLE-ENDIAN in .v4Add.Val (native uint32)
     * with .v[0..3] = octets in network order (MSB first).  Use the octet
     * form so we work regardless of endianness. */
    remote.v4Add.v[0] = (uint8_t)((s_master_ip_host >> 24) & 0xFFu);
    remote.v4Add.v[1] = (uint8_t)((s_master_ip_host >> 16) & 0xFFu);
    remote.v4Add.v[2] = (uint8_t)((s_master_ip_host >>  8) & 0xFFu);
    remote.v4Add.v[3] = (uint8_t)( s_master_ip_host        & 0xFFu);

    s_sock = TCPIP_UDP_ClientOpen(IP_ADDRESS_TYPE_IPV4, SW_NTP_UDP_PORT, &remote);
    if (s_sock == INVALID_UDP_SOCKET) {
        return false;
    }
    SYS_CONSOLE_PRINT("[SW-NTP] follower targeting %u.%u.%u.%u:%u\r\n",
                      (unsigned)remote.v4Add.v[0], (unsigned)remote.v4Add.v[1],
                      (unsigned)remote.v4Add.v[2], (unsigned)remote.v4Add.v[3],
                      (unsigned)SW_NTP_UDP_PORT);
    return true;
}

static bool sw_ntp_ensure_socket_open(void)
{
    if (s_sock != INVALID_UDP_SOCKET) {
        return true;
    }
    if (!sw_ntp_stack_is_up()) {
        return false;
    }
    switch (s_mode) {
        case SW_NTP_MASTER:   return sw_ntp_open_master();
        case SW_NTP_FOLLOWER: return sw_ntp_open_follower();
        default:              return false;
    }
}

/* -------------------------------------------------------------------------
 * Master: one iteration — drain RX queue, respond to each request.
 * ---------------------------------------------------------------------- */

static void sw_ntp_master_service(void)
{
    while (TCPIP_UDP_GetIsReady(s_sock) >= (uint16_t)sizeof(sw_ntp_pkt_t)) {
        /* Grab t2 as close to the RX event as this layer allows — i.e.
         * immediately after the stack reports data-available.  This is the
         * whole point of the test: t2 carries all SW latencies that
         * accumulated between the real RX on the wire and us reading it. */
        int64_t t2 = (int64_t)PTP_CLOCK_GetTime_ns();

        sw_ntp_pkt_t pkt;
        uint16_t got = TCPIP_UDP_ArrayGet(s_sock, (uint8_t *)&pkt,
                                          (uint16_t)sizeof(pkt));
        if (got != (uint16_t)sizeof(pkt)) {
            (void)TCPIP_UDP_Discard(s_sock);
            continue;
        }
        if (pkt.type != (uint8_t)SW_NTP_PKT_REQ) {
            /* Ignore stray responses or garbage */
            continue;
        }

        /* Build response.  t1 is echoed back so the follower can pair
         * this response with its original request (bulletproofs against
         * packet reordering in the rare case it ever happens). */
        pkt.type = (uint8_t)SW_NTP_PKT_RESP;
        pkt.t2_ns = t2;

        if (TCPIP_UDP_TxPutIsReady(s_sock, (uint16_t)sizeof(pkt)) <
            (uint16_t)sizeof(pkt))
        {
            if (s_verbose) {
                SYS_CONSOLE_PRINT("[SW-NTP-M] TX buffer full, dropping seq=%u\r\n",
                                  (unsigned)pkt.seq);
            }
            continue;
        }

        /* t3 captured as late as possible before the actual send */
        pkt.t3_ns = (int64_t)PTP_CLOCK_GetTime_ns();

        (void)TCPIP_UDP_ArrayPut(s_sock, (const uint8_t *)&pkt,
                                 (uint16_t)sizeof(pkt));
        (void)TCPIP_UDP_Flush(s_sock);

        s_gm_responses++;
        if (s_verbose) {
            SYS_CONSOLE_PRINT("[SW-NTP-M] resp seq=%u t2=%lld t3=%lld\r\n",
                              (unsigned)pkt.seq,
                              (long long)pkt.t2_ns, (long long)pkt.t3_ns);
        }
    }
}

/* -------------------------------------------------------------------------
 * Follower: poll ~1 Hz, stamp t1/t4, compute offset, push to ring buffer.
 * ---------------------------------------------------------------------- */

static void sw_ntp_follower_send_request(uint64_t now_tick)
{
    if (TCPIP_UDP_TxPutIsReady(s_sock, (uint16_t)sizeof(sw_ntp_pkt_t)) <
        (uint16_t)sizeof(sw_ntp_pkt_t))
    {
        /* TX not ready — try again next tick */
        return;
    }

    sw_ntp_pkt_t pkt;
    memset(&pkt, 0, sizeof(pkt));
    pkt.type  = (uint8_t)SW_NTP_PKT_REQ;
    pkt.seq   = ++s_fol_seq;
    pkt.t1_ns = (int64_t)PTP_CLOCK_GetTime_ns();

    (void)TCPIP_UDP_ArrayPut(s_sock, (const uint8_t *)&pkt,
                             (uint16_t)sizeof(pkt));
    (void)TCPIP_UDP_Flush(s_sock);

    s_fol_pending_seq         = pkt.seq;
    s_fol_request_outstanding = true;
    s_fol_last_tx_tick        = now_tick;
    s_fol_pending_deadline_tick = now_tick +
        (uint64_t)SW_NTP_RESP_TIMEOUT_MS * ticks_per_ms_cached();

    if (s_verbose) {
        SYS_CONSOLE_PRINT("[SW-NTP-F] req  seq=%u t1=%lld\r\n",
                          (unsigned)pkt.seq, (long long)pkt.t1_ns);
    }
}

static void sw_ntp_follower_handle_response(void)
{
    while (TCPIP_UDP_GetIsReady(s_sock) >= (uint16_t)sizeof(sw_ntp_pkt_t)) {
        /* t4 as close to the RX event as possible */
        int64_t t4 = (int64_t)PTP_CLOCK_GetTime_ns();

        sw_ntp_pkt_t pkt;
        uint16_t got = TCPIP_UDP_ArrayGet(s_sock, (uint8_t *)&pkt,
                                          (uint16_t)sizeof(pkt));
        if (got != (uint16_t)sizeof(pkt)) {
            (void)TCPIP_UDP_Discard(s_sock);
            continue;
        }
        if (pkt.type != (uint8_t)SW_NTP_PKT_RESP) {
            continue;
        }
        if (!s_fol_request_outstanding || pkt.seq != s_fol_pending_seq) {
            /* Stale response, ignore */
            if (s_verbose) {
                SYS_CONSOLE_PRINT("[SW-NTP-F] stale resp seq=%u expected=%u\r\n",
                                  (unsigned)pkt.seq,
                                  (unsigned)s_fol_pending_seq);
            }
            continue;
        }

        /* NTP offset formula (identical to PTP):
         *   offset = ((t2 - t1) + (t3 - t4)) / 2        */
        int64_t offset = ((pkt.t2_ns - pkt.t1_ns) +
                          (pkt.t3_ns - t4)) / 2;

        s_fol_last_offset_ns = offset;
        s_fol_samples++;
        s_fol_request_outstanding = false;
        sw_ntp_offset_trace_record(offset, 1u);

        if (s_verbose) {
            int64_t rtt = (t4 - pkt.t1_ns) - (pkt.t3_ns - pkt.t2_ns);
            SYS_CONSOLE_PRINT(
                "[SW-NTP-F] resp seq=%u t1=%lld t2=%lld t3=%lld t4=%lld "
                "offset=%lld rtt=%lld\r\n",
                (unsigned)pkt.seq,
                (long long)pkt.t1_ns, (long long)pkt.t2_ns,
                (long long)pkt.t3_ns, (long long)t4,
                (long long)offset,   (long long)rtt);
        }
    }
}

static void sw_ntp_follower_service(void)
{
    uint64_t now_tick = SYS_TIME_Counter64Get();
    uint64_t tpm      = ticks_per_ms_cached();

    /* Drain any incoming responses first — t4 stamped immediately on read. */
    sw_ntp_follower_handle_response();

    if (s_fol_request_outstanding) {
        /* Timeout check */
        if ((int64_t)(now_tick - s_fol_pending_deadline_tick) >= 0) {
            s_fol_timeouts++;
            sw_ntp_offset_trace_record(0, 0u);
            s_fol_request_outstanding = false;
            if (s_verbose) {
                SYS_CONSOLE_PRINT("[SW-NTP-F] timeout seq=%u\r\n",
                                  (unsigned)s_fol_pending_seq);
            }
        }
        return;
    }

    /* No request pending: fire next one when poll interval elapsed.
     * First poll fires immediately after mode switch (s_fol_last_tx_tick == 0). */
    uint64_t interval_tick = (uint64_t)s_poll_interval_ms * tpm;
    if (s_fol_last_tx_tick == 0u ||
        (now_tick - s_fol_last_tx_tick) >= interval_tick)
    {
        sw_ntp_follower_send_request(now_tick);
    }
}

/* -------------------------------------------------------------------------
 * Public API
 * ---------------------------------------------------------------------- */

void sw_ntp_init(void)
{
    s_mode = SW_NTP_OFF;
    s_sock = INVALID_UDP_SOCKET;
    s_fol_seq                 = 0u;
    s_fol_request_outstanding = false;
    s_fol_last_tx_tick        = 0u;
    s_fol_samples             = 0u;
    s_fol_timeouts            = 0u;
    s_fol_last_offset_ns      = 0;
    s_gm_responses            = 0u;
}

void sw_ntp_service(void)
{
    if (s_mode == SW_NTP_OFF) {
        return;
    }
    if (!sw_ntp_ensure_socket_open()) {
        return;
    }
    if (s_mode == SW_NTP_MASTER) {
        sw_ntp_master_service();
    } else {
        sw_ntp_follower_service();
    }
}

void sw_ntp_set_mode(sw_ntp_mode_t mode)
{
    if (mode == s_mode) {
        return;
    }
    /* Teardown old socket on any mode change */
    sw_ntp_close_socket();
    s_mode                    = mode;
    s_fol_seq                 = 0u;
    s_fol_request_outstanding = false;
    s_fol_last_tx_tick        = 0u;
}

sw_ntp_mode_t sw_ntp_get_mode(void)
{
    return s_mode;
}

void sw_ntp_set_master_ip(uint32_t ipv4_host_order)
{
    if (ipv4_host_order != s_master_ip_host) {
        s_master_ip_host = ipv4_host_order;
        if (s_mode == SW_NTP_FOLLOWER) {
            /* Re-open against the new IP */
            sw_ntp_close_socket();
            s_fol_request_outstanding = false;
            s_fol_last_tx_tick        = 0u;
        }
    }
}

void sw_ntp_set_poll_interval_ms(uint32_t ms)
{
    if (ms < 10u)   ms = 10u;
    if (ms > 10000u) ms = 10000u;
    s_poll_interval_ms = ms;
}

uint32_t sw_ntp_get_poll_interval_ms(void)
{
    return s_poll_interval_ms;
}

void sw_ntp_set_verbose(bool verbose)
{
    s_verbose = verbose;
}

void sw_ntp_get_stats(uint32_t *samples_out,
                      uint32_t *timeouts_out,
                      int64_t  *last_offset_ns_out)
{
    if (samples_out)        *samples_out        = s_fol_samples;
    if (timeouts_out)       *timeouts_out       = s_fol_timeouts;
    if (last_offset_ns_out) *last_offset_ns_out = s_fol_last_offset_ns;
}
