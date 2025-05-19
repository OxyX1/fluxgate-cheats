import ctypes
import threading
import time
import mss
import requests
from io import BytesIO
from PIL import Image
from dearpygui.core import *
from dearpygui.simple import *

# Constants for Windows drawing
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
hwnd = user32.GetDesktopWindow()
hdc = user32.GetDC(hwnd)

# Roboflow API
API_URL = "https://detect.roboflow.com/roblox-rl9yb/1"
API_KEY = "fB8m5Ld9T0wenu82q0C4"

# Detected objects
detections = []

def capture_screen():
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        img = sct.grab(monitor)
        return Image.frombytes("RGB", img.size, img.rgb)

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
                x = obj["x"]
                y = obj["y"]
                w = obj["width"]
                h = obj["height"]
                new_detections.append((x, y, w, h))
            detections = new_detections

        except Exception as e:
            print("[ERROR] Detection failed:", e)

        time.sleep(1)  # 1 FPS detection

def draw_esp_boxes():
    screen_w = user32.GetSystemMetrics(0)
    screen_h = user32.GetSystemMetrics(1)

    pen = gdi32.CreatePen(0, 2, 0x00FF00)  # Green RGB
    gdi32.SelectObject(hdc, pen)

    for (x, y, w, h) in detections:
        # Convert normalized YOLO-like coords to absolute screen coords
        left = int(x - w / 2)
        top = int(y - h / 2)
        right = int(x + w / 2)
        bottom = int(y + h / 2)

        gdi32.Rectangle(hdc, left, top, right, bottom)

    gdi32.DeleteObject(pen)

def start_esp_loop():
    while True:
        draw_esp_boxes()
        time.sleep(1 / 60)  # ~60 FPS

def setup_menu():
    set_main_window_title("FluxGate")
    set_main_window_size(300, 200)
    with window("FluxGate Menu", width=280, height=180):
        add_text("FluxGate", color=[255, 0, 255])
        add_button("Enable ESP", callback=lambda: print("ESP Enabled"))
        add_button("Optimize PC", callback=lambda: print("Optimize"))
        add_button("Unbloat PC", callback=lambda: print("Unbloat"))

    start_dearpygui()

def main():
    # Run detection and ESP in background
    threading.Thread(target=run_detection, daemon=True).start()
    threading.Thread(target=start_esp_loop, daemon=True).start()

    # Start the UI
    setup_menu()

if __name__ == "__main__":
    main()
