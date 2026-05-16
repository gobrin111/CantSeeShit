import tkinter as tk
import threading
import keyboard

from magnifier import Magnifier

from Config import config

mag = Magnifier()

# def start_mag():
#     threading.Thread(target=mag.run, daemon=True).start()

threading.Thread(target=mag.run, daemon=True).start()

def toggle():
    mag.toggle()

root = tk.Tk()
root.geometry("300x250")
root.title("FPS Magnifier")

# start_btn = tk.Button(root, text="Start", command=start_mag)
# start_btn.pack(pady=10)

toggle_btn = tk.Button(root, text="Toggle Magnifier", command=toggle)
toggle_btn.pack(pady=10)

zoom_slider = tk.Scale(
    root,
    from_=1.5,
    to=6.0,
    resolution=0.25,
    orient="horizontal",
    label="Zoom"
)
zoom_slider.set(2.0)
zoom_slider.pack(pady=10)

def update_zoom(val):
    mag.zoom = float(val)

zoom_slider.configure(command=update_zoom)


waiting_for_key = False


def set_new_key(event):
    global waiting_for_key

    if not waiting_for_key:
        return

    # Keyboard key
    config.TOGGLE_KEY = str(event.name)
    mag.update_hotkey()

    keybind_button.config(text=f"Toggle Key: {config.TOGGLE_KEY}")

    waiting_for_key = False


def start_rebind():
    global waiting_for_key

    waiting_for_key = True

    keybind_button.config(text="Press any key...")

    # Listen for next key press
    keyboard.on_press(set_new_key)


keybind_button = tk.Button(
    root,
    text=f"Toggle Key: {config.TOGGLE_KEY}",
    command=start_rebind,
    width=20,
    height=2
)

keybind_button.pack(pady=40)

root.mainloop()