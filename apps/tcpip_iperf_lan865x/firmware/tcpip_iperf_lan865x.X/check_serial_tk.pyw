import serial.tools.list_ports
import tkinter as tk
from tkinter import scrolledtext

def update_ports():
    clear_text()
    show_ports()
    root.after(2000, update_ports)  # Funktion alle 2 Sekunden aufrufen

def get_com_port_number(port_info):
    # Extrahiere die COM-Port-Nummer aus der Port-Information
    for part in port_info.device.split('COM'):
        if part.isdigit():
            return int(part)
    return float('inf')  # Fallback-Wert für Ports ohne COM-Port-Nummer

def show_ports():
    com_ports = serial.tools.list_ports.comports()
    sorted_ports = sorted(com_ports, key=get_com_port_number)
    for port_info in sorted_ports:
        append_text(f"Port Info: {port_info}\n")
        append_text(f"Device: {port_info.device}\n")
        # append_text(f"Name: {port_info.name}\n")
        append_text(f"Description: {port_info.description}\n")
        # append_text(f"Interface: {port_info.interface}\n")
        append_text(f"Location: {port_info.location}\n")
        append_text(f"Manufacturer: {port_info.manufacturer}\n")
        # append_text(f"Product: {port_info.product}\n")
        append_text(f"Serial Number: {port_info.serial_number}\n")
        append_text(f"Vendor ID: {hex(port_info.vid) if port_info.vid is not None else 'N/A'} ")
        append_text(f"Product ID: {hex(port_info.pid) if port_info.pid is not None else 'N/A'}\n")
        append_text("=" * 50 + "\n")

def clear_text():
    info_text.delete(1.0, tk.END)

def append_text(text):
    info_text.insert(tk.END, text)

# Erstelle das Hauptfenster der GUI
root = tk.Tk()
root.title("COM Ports Info")

# Erstelle ein Textfeld zum Anzeigen der Informationen
info_text = scrolledtext.ScrolledText(root, width=40, height=31)
info_text.grid(row=0, column=0, columnspan=2)

# Erstelle einen Button zum Ausführen der Funktion
show_button = tk.Button(root, text="Show COM Ports", command=show_ports)
show_button.grid(row=1, column=0)

# Erstelle einen Button zum Leeren des Textfelds
clear_button = tk.Button(root, text="Clear Window", command=clear_text)
clear_button.grid(row=1, column=1)

# Erstelle ein Textfeld zum Anzeigen der Informationen
info_text = scrolledtext.ScrolledText(root, width=50, height=33)
info_text.grid(row=0, column=0, columnspan=2)
info_text.configure(font=("Helvetica", 12, "bold"), bg="black", fg="light green")  # Hintergrund und Vordergrund setzen

# Starte die Aktualisierungsfunktion
update_ports()


# Starte die GUI-Schleife
root.mainloop()
