import ctypes
import threading
import time
import mss
import requests
from io import BytesIO
from PIL import Image
from dearpygui.core import *
from dearpygui.simple import *

# Windows API stuff
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
hwnd = user32.GetDesktopWindow()
hdc = user32.GetDC(hwnd)

# Roboflow
API_URL = "https://detect.roboflow.com/roblox-rl9yb/1"
API_KEY = "fB8m5Ld9T0wenu82q0C4"

detections = []
esp_enabled = False  # ESP toggle

# Screen capture
def capture_screen():
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        img = sct.grab(monitor)
        return Image.frombytes("RGB", img.size, img.rgb)

# Detection thread
def run_detection():
    global detections
    while True:
        try:
            img = capture_screen()
            buf = BytesIO()
            img.save(buf, format='JPEG')
            buf.seek(0)

            headers = {
                "Content-Type": "application/octet-stream",
                "x-api-key": API_KEY
            }

            resp = requests.post(API_URL, headers=headers, data=buf.read())
            resp.raise_for_status()

            data = resp.json()
            new_detections = []
            for obj in data.get("predictions", []):
                x, y, w, h = obj["x"], obj["y"], obj["width"], obj["height"]
                new_detections.append((x, y, w, h))
            detections = new_detections

        except Exception as e:
            print("[ERROR] Detection failed:", e)

        time.sleep(1)  # 1 FPS

# ESP drawing thread
def draw_esp_boxes():
    screen_w = user32.GetSystemMetrics(0)
    screen_h = user32.GetSystemMetrics(1)

    pen = gdi32.CreatePen(0, 2, 0x00FF00)  # Green box
    gdi32.SelectObject(hdc, pen)

    for (x, y, w, h) in detections:
        left = int(x - w / 2)
        top = int(y - h / 2)
        right = int(x + w / 2)
        bottom = int(y + h / 2)
        gdi32.Rectangle(hdc, left, top, right, bottom)

    gdi32.DeleteObject(pen)

def start_esp_loop():
    while True:
        if esp_enabled:
            draw_esp_boxes()
        time.sleep(1 / 60)  # 60 FPS

# ESP toggle logic
def toggle_esp(sender, data):
    global esp_enabled
    esp_enabled = not esp_enabled
    set_item_label("ESP_Button", "Disable ESP" if esp_enabled else "Enable ESP")
    print(f"ESP {'Enabled' if esp_enabled else 'Disabled'}")

# UI setup
def setup_menu():
    set_main_window_title("FluxGate")
    set_main_window_size(300, 200)
    set_theme("Dark")

    with window("FluxGate Menu", width=280, height=180):
        add_text("FluxGate", color=[255, 0, 255])
        add_spacing(count=2)
        add_button("Enable ESP", callback=toggle_esp, tag="ESP_Button")
        add_spacing(count=2)
        add_text("Detection by Roboflow")
        add_text("v1.0", color=[128, 128, 128])

    start_dearpygui()

# Main entry
def main():
    threading.Thread(target=run_detection, daemon=True).start()
    threading.Thread(target=start_esp_loop, daemon=True).start()
    setup_menu()

if __name__ == "__main__":
    main()
