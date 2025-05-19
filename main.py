import pyautogui
import numpy as np
import cv2
import time

def load_config(path="config.txt"):
    config = {}
    with open(path, "r") as file:
        for line in file:
            line = line.strip()
            if line and not line.startswith("#"):
                key, value = line.split("=")
                key = key.strip()
                value = value.strip()
                if "," in value:
                    config[key] = tuple(map(int, value.split(",")))
                else:
                    config[key] = int(value)
    return config

def find_target_center(img, color, tolerance=20):
    lower = np.array([max(c - tolerance, 0) for c in color])
    upper = np.array([min(c + tolerance, 255) for c in color])
    mask = cv2.inRange(img, lower, upper)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        largest = max(contours, key=cv2.contourArea)
        M = cv2.moments(largest)
        if M["m00"] != 0:
            cx = int(M["m10"] / M["m00"])
            cy = int(M["m01"] / M["m00"])
            return cx, cy
    return None

def aimbot(config):
    screen = pyautogui.screenshot()
    frame = np.array(screen)
    frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    
    pos = find_target_center(frame, config["target_color"], config["tolerance"])
    if pos:
        screen_w, screen_h = pyautogui.size()
        target_x = pos[0] + config["offsetX"]
        target_y = pos[1] + config["offsetY"]
        dx = target_x - screen_w // 2
        dy = target_y - screen_h // 2
        pyautogui.moveRel(dx // 10, dy // 10, duration=0.01)

# === Main Loop ===
config = load_config()
print("Aimbot running. Press Ctrl+C to stop.")
try:
    while True:
        aimbot(config)
        time.sleep(0.01)
except KeyboardInterrupt:
    print("Aimbot stopped.")
