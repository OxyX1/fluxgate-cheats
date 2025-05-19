import dearpygui.dearpygui as dpg
import mss
import numpy as np
from PIL import Image
import cv2 # OpenCV for template matching
import ctypes
import win32gui
import win32con
import win32api
import threading
import time
import os

# --- User32 and GDI32 functions ---
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

# Constants for GDI
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0

# Window styles for overlay
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000 # Important so it doesn't take focus

# --- Configuration Store (shared between threads) ---
config = {
    "esp_enabled": False,
    "color_esp_enabled": True,
    "image_esp_enabled": False,
    "target_color_rgb": [255, 0, 0],  # Default Red
    "tolerance": 30,
    "offset_x": 0,
    "offset_y": 0,
    "target_window_title": "Roblox", # Or the exact window title
    "template_image_path": "",
    "template_threshold": 0.8,
    "draw_box_color_rgb": [0, 255, 0], # Green for ESP boxes
    "draw_text_color_rgb": [255, 255, 255] # White for text
}
config_lock = threading.Lock()
detected_entities = [] # List of (x, y, w, h, label, type_color_tuple) for drawing

# --- Overlay Window Class ---
class GDIOverlay:
    def __init__(self):
        self.hwnd = None
        self.hdc_mem = None
        self.hbm_mem = None
        self.hbrush_box = None
        self.hfont = None
        self.old_hbm = None
        self.overlay_active = False
        self.screen_width = user32.GetSystemMetrics(0)
        self.screen_height = user32.GetSystemMetrics(1)
        self.target_game_hwnd = None
        self.game_rect = (0,0,0,0) # (left, top, right, bottom)

    def create_window(self):
        wc = win32gui.WNDCLASS()
        wc.hInstance = win32api.GetModuleHandle(None)
        wc.lpszClassName = "GDIOverlayWindow"
        wc.lpfnWndProc = self._wnd_proc
        class_atom = win32gui.RegisterClass(wc)

        # Create a fullscreen, transparent, topmost, non-activatable window
        self.hwnd = win32gui.CreateWindowEx(
            WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_NOACTIVATE,
            class_atom,
            "GDIOverlayWindow", # Window title (not visible)
            win32con.WS_POPUP, # No border, title bar, etc.
            0, 0, self.screen_width, self.screen_height, # Fullscreen initially
            None, None, wc.hInstance, None
        )

        if not self.hwnd:
            print(f"Failed to create overlay window. Error: {win32api.GetLastError()}")
            return False

        # Set transparency (e.g., make black transparent)
        # Using 0 for color key and LWA_ALPHA for per-pixel alpha is better for smooth drawing
        win32gui.SetLayeredWindowAttributes(self.hwnd, 0, 255, win32con.LWA_ALPHA) # Full opacity, per-pixel alpha
        # win32gui.SetLayeredWindowAttributes(self.hwnd, win32api.RGB(0,0,0), 0, win32con.LWA_COLORKEY) # Makes black transparent

        win32gui.ShowWindow(self.hwnd, win32con.SW_SHOW)
        win32gui.UpdateWindow(self.hwnd) # Process paint messages etc.
        
        # Ensure it's on top
        win32gui.SetWindowPos(self.hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, 
                              win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)

        # Create GDI resources
        hdc_screen = user32.GetDC(None)
        self.hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        self.hbm_mem = gdi32.CreateCompatibleBitmap(hdc_screen, self.screen_width, self.screen_height)
        self.old_hbm = gdi32.SelectObject(self.hdc_mem, self.hbm_mem)
        user32.ReleaseDC(None, hdc_screen)

        # Create a transparent brush for drawing boxes (hollow)
        # LOGBRUSH for hollow brush
        logBrush = win32gui.LOGBRUSH()
        logBrush.lbStyle = win32con.BS_HOLLOW
        self.hbrush_box = gdi32.CreateBrushIndirect(logBrush)
        
        # Create font
        # For simplicity, using system default. For custom font: CreateFontIndirect
        self.hfont = gdi32.GetStockObject(win32con.DEFAULT_GUI_FONT)


        self.overlay_active = True
        print("Overlay window created successfully.")
        return True

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        # Minimal window procedure, primarily for WM_PAINT if not using UpdateLayeredWindow directly
        # if msg == win32con.WM_PAINT:
        #     # This will be handled by update_frame
        #     return 0
        if msg == win32con.WM_DESTROY:
            self.cleanup()
            win32gui.PostQuitMessage(0) # Important if this thread runs a message loop
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def update_target_window(self, window_title):
        self.target_game_hwnd = win32gui.FindWindow(None, window_title)
        if self.target_game_hwnd:
            self.game_rect = win32gui.GetWindowRect(self.target_game_hwnd)
            # Optional: Resize overlay to match game window if not fullscreen
            # For simplicity, we keep overlay fullscreen and draw only within game_rect bounds
            # win32gui.SetWindowPos(self.hwnd, win32con.HWND_TOPMOST,
            #                       self.game_rect[0], self.game_rect[1],
            #                       self.game_rect[2] - self.game_rect[0],
            #                       self.game_rect[3] - self.game_rect[1],
            #                       win32con.SWP_NOACTIVATE)
            return True
        else:
            self.game_rect = (0,0,0,0) # Reset if window not found
            return False

    def update_frame(self, entities_to_draw):
        if not self.overlay_active or not self.hwnd or not self.hdc_mem:
            return

        # 1. Clear the offscreen bitmap (fill with a color that will be transparent, or fully transparent)
        # Filling with black, if LWA_COLORKEY is set to black.
        # If using LWA_ALPHA, fill with a color with 0 alpha channel if possible, or just clear.
        # Here, we clear to fully transparent black for LWA_ALPHA.
        gdi32.SelectObject(self.hdc_mem, self.hbm_mem) # Ensure our bitmap is selected
        # Create a brush for clearing (black)
        hbrush_clear = gdi32.CreateSolidBrush(win32api.RGB(0,0,0)) # Black brush
        rect_clear = (0, 0, self.screen_width, self.screen_height)
        user32.FillRect(self.hdc_mem, ctypes.byref(ctypes.wintypes.RECT(*rect_clear)), hbrush_clear)
        gdi32.DeleteObject(hbrush_clear)


        # 2. Draw entities onto the offscreen bitmap (self.hdc_mem)
        old_font = gdi32.SelectObject(self.hdc_mem, self.hfont)
        old_brush = gdi32.SelectObject(self.hdc_mem, self.hbrush_box) # Use hollow brush for rectangles
        
        gdi32.SetBkMode(self.hdc_mem, win32con.TRANSPARENT) # Transparent background for text

        for x, y, w, h, label, box_color_tuple in entities_to_draw:
            # Ensure drawing occurs within screen bounds
            if x + w > self.screen_width or y + h > self.screen_height or x < 0 or y < 0:
                continue
            
            # Set pen color for rectangle border
            r, g, b = box_color_tuple
            pen_color = win32api.RGB(r, g, b)
            hpen = gdi32.CreatePen(win32con.PS_SOLID, 1, pen_color) # 1 pixel solid pen
            old_pen = gdi32.SelectObject(self.hdc_mem, hpen)

            # Draw rectangle
            gdi32.Rectangle(self.hdc_mem, x, y, x + w, y + h)
            
            gdi32.SelectObject(self.hdc_mem, old_pen) # Restore old pen
            gdi32.DeleteObject(hpen) # Delete created pen

            # Draw label if any
            if label:
                text_r, text_g, text_b = config["draw_text_color_rgb"]
                gdi32.SetTextColor(self.hdc_mem, win32api.RGB(text_r, text_g, text_b))
                gdi32.TextOutW(self.hdc_mem, x, y - 15, label, len(label)) # TextOutW for Unicode

        gdi32.SelectObject(self.hdc_mem, old_font) # Restore old font
        gdi32.SelectObject(self.hdc_mem, old_brush) # Restore old brush

        # 3. Update the layered window
        source_pos = ctypes.wintypes.POINT(0, 0)
        screen_pos = ctypes.wintypes.POINT(0, 0) # Overlay is fullscreen, so 0,0
        size = ctypes.wintypes.SIZE(self.screen_width, self.screen_height)
        blend_op = win32con.AC_SRC_OVER # Source over destination
        
        # Using 0x01 for LWA_ALPHA (per-pixel alpha)
        # Using 0x02 for LWA_COLORKEY (transparent color key)
        # We used LWA_ALPHA (per-pixel alpha) in SetLayeredWindowAttributes
        success = user32.UpdateLayeredWindow(
            self.hwnd, None, ctypes.byref(screen_pos), ctypes.byref(size),
            self.hdc_mem, ctypes.byref(source_pos), 0, # 0 for crKey for LWA_ALPHA
            ctypes.byref(ctypes.c_ulong(win32con.AC_SRC_ALPHA | blend_op)), # BLENDFUNCTION
            win32con.ULW_ALPHA # Use alpha channel
        )
        if not success:
             print(f"UpdateLayeredWindow failed: {win32api.GetLastError()}")


    def cleanup(self):
        self.overlay_active = False
        if self.hbrush_box:
            gdi32.DeleteObject(self.hbrush_box)
            self.hbrush_box = None
        if self.hdc_mem:
            if self.old_hbm:
                gdi32.SelectObject(self.hdc_mem, self.old_hbm) # Restore old bitmap before deleting
            gdi32.DeleteObject(self.hbm_mem)
            gdi32.DeleteDC(self.hdc_mem)
            self.hdc_mem = None
            self.hbm_mem = None
        if self.hwnd:
            win32gui.DestroyWindow(self.hwnd)
            self.hwnd = None
        # Unregister class if this is the last window of this class
        # For simplicity, we might skip unregistering if app closes soon after.
        # win32gui.UnregisterClass("GDIOverlayWindow", win32api.GetModuleHandle(None))
        print("Overlay cleaned up.")

overlay_instance = GDIOverlay()
stop_event = threading.Event()

# --- ESP Logic Thread ---
def esp_worker():
    global detected_entities, config
    sct = mss.mss()
    template = None
    template_w, template_h = 0, 0

    while not stop_event.is_set():
        with config_lock:
            esp_on = config["esp_enabled"]
            target_title = config["target_window_title"]
            color_esp_on = config["color_esp_enabled"]
            target_rgb = np.array(config["target_color_rgb"], dtype=np.uint8)
            tolerance_val = config["tolerance"]
            offset_x_val, offset_y_val = config["offset_x"], config["offset_y"]
            image_esp_on = config["image_esp_enabled"]
            template_path = config["template_image_path"]
            template_thresh = config["template_threshold"]
            draw_box_clr = tuple(config["draw_box_color_rgb"]) # For passing to drawing

        if not esp_on:
            detected_entities = [] # Clear entities when ESP is off
            time.sleep(0.1)
            continue

        # Find the target window and get its dimensions
        hwnd = win32gui.FindWindow(None, target_title)
        if not hwnd:
            if 'Roblox' in target_title and not overlay_instance.target_game_hwnd: # Only print once
                 dpg_set_status("Roblox window not found. Make sure it's running and title matches.")
            detected_entities = []
            overlay_instance.update_target_window(None) # Signal no target
            time.sleep(1)
            continue
        
        if not overlay_instance.target_game_hwnd or overlay_instance.target_game_hwnd != hwnd:
            overlay_instance.update_target_window(target_title) # Update overlay target
            dpg_set_status(f"Tracking: {target_title}")


        game_rect = win32gui.GetWindowRect(hwnd) # (left, top, right, bottom)
        # Check if window is minimized
        if game_rect[0] == -32000 and game_rect[1] == -32000:
            dpg_set_status(f"{target_title} is minimized.")
            detected_entities = []
            time.sleep(0.5)
            continue

        monitor = {"top": game_rect[1], "left": game_rect[0], 
                   "width": game_rect[2] - game_rect[0], 
                   "height": game_rect[3] - game_rect[1]}
        
        if monitor["width"] <=0 or monitor["height"] <=0: # Skip if window is invalid size
            detected_entities = []
            time.sleep(0.1)
            continue

        img_pil = sct.grab(monitor)
        img_np = np.array(img_pil, dtype=np.uint8)[:, :, :3] # Convert to BGR numpy array, drop alpha

        current_detected = []

        # 1. Color ESP
        if color_esp_on:
            # OpenCV uses BGR, mss gives RGB. Convert target_rgb to BGR if using OpenCV directly
            # For numpy comparison, ensure consistent ordering. sct.grab gives RGB.
            # Our target_rgb is also RGB from DPG.
            lower_bound = np.clip(target_rgb - tolerance_val, 0, 255)
            upper_bound = np.clip(target_rgb + tolerance_val, 255, 255) # Clip upper to 255

            mask = cv2.inRange(img_np, lower_bound, upper_bound)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            for contour in contours:
                if cv2.contourArea(contour) > 50: # Min area to filter noise
                    x, y, w, h = cv2.boundingRect(contour)
                    # Convert to screen coordinates
                    screen_x = game_rect[0] + x + offset_x_val
                    screen_y = game_rect[1] + y + offset_y_val
                    current_detected.append((screen_x, screen_y, w, h, "Color", draw_box_clr))
        
        # 2. Image ESP (Template Matching)
        if image_esp_on and template_path:
            if template is None or config["template_image_path"] != getattr(esp_worker, "last_template_path", ""):
                try:
                    template_pil = Image.open(template_path).convert('RGB')
                    template = np.array(template_pil, dtype=np.uint8)
                    # template = cv2.cvtColor(template, cv2.COLOR_RGB2BGR) # OpenCV uses BGR
                    template_h, template_w = template.shape[:2]
                    esp_worker.last_template_path = template_path
                    dpg_set_status(f"Template loaded: {os.path.basename(template_path)}")
                except Exception as e:
                    dpg_set_status(f"Error loading template: {e}")
                    template = None
            
            if template is not None and template_w > 0 and template_h > 0:
                # img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR) # Convert frame to BGR for OpenCV
                # Match template (img_np is RGB, template is RGB)
                res = cv2.matchTemplate(img_np, template, cv2.TM_CCOEFF_NORMED)
                loc = np.where(res >= template_thresh)
                
                # To avoid overlapping boxes for multiple close matches for the same object
                # A simple non-maximum suppression (NMS) could be implemented here.
                # For now, just draw all matches.
                processed_rects = [] # For basic NMS
                min_dist_sq = (template_w * 0.5)**2 + (template_h * 0.5)**2 # Min distance between centers

                for pt in zip(*loc[::-1]): # Switch x and y
                    # Check for overlap with already added rects
                    is_overlapping = False
                    for pr_x, pr_y, pr_w, pr_h in processed_rects:
                        # Simple center distance check (could be improved with IoU)
                        center_x, center_y = pt[0] + template_w//2, pt[1] + template_h//2
                        pr_center_x, pr_center_y = pr_x + pr_w//2, pr_y + pr_h//2
                        dist_sq = (center_x - pr_center_x)**2 + (center_y - pr_center_y)**2
                        if dist_sq < min_dist_sq:
                            is_overlapping = True
                            break
                    if is_overlapping:
                        continue

                    processed_rects.append((pt[0], pt[1], template_w, template_h))
                    
                    screen_x = game_rect[0] + pt[0] + offset_x_val
                    screen_y = game_rect[1] + pt[1] + offset_y_val
                    current_detected.append((screen_x, screen_y, template_w, template_h, "Image", draw_box_clr))

        detected_entities = current_detected # Update shared list
        time.sleep(0.01) # Adjust for desired FPS, balances CPU usage

    sct.close()
    print("ESP worker stopped.")


# --- Dear PyGUI Callbacks and Setup ---
def dpg_set_status(message):
    if dpg.is_dearpygui_running():
        dpg.set_value("status_text", message)

def toggle_esp(sender, app_data):
    with config_lock:
        config["esp_enabled"] = app_data
    dpg_set_status(f"ESP {'Enabled' if app_data else 'Disabled'}")

def toggle_color_esp(sender, app_data):
    with config_lock:
        config["color_esp_enabled"] = app_data

def toggle_image_esp(sender, app_data):
    with config_lock:
        config["image_esp_enabled"] = app_data

def set_target_color(sender, app_data):
    # app_data is a list [r, g, b, a] from 0-1.0 float, convert to 0-255 int for RGB
    with config_lock:
        config["target_color_rgb"] = [int(c * 255) for c in app_data[:3]]
    # Update the color preview next to the picker
    dpg.set_value("color_preview_rect_color", [c for c in app_data[:3]] + [1.0])


def set_tolerance(sender, app_data):
    with config_lock:
        config["tolerance"] = app_data

def set_offset_x(sender, app_data):
    with config_lock:
        config["offset_x"] = app_data

def set_offset_y(sender, app_data):
    with config_lock:
        config["offset_y"] = app_data

def set_target_window(sender, app_data):
    with config_lock:
        config["target_window_title"] = app_data
    overlay_instance.update_target_window(app_data) # Try to find new window immediately

def set_template_threshold(sender, app_data):
    with config_lock:
        config["template_threshold"] = app_data

def select_template_image_callback(sender, app_data):
    if "file_path_name" in app_data:
        with config_lock:
            config["template_image_path"] = app_data["file_path_name"]
        dpg.set_value("template_path_text", f"Template: {os.path.basename(app_data['file_path_name'])}")
    else:
        dpg_set_status("No template image selected or dialog cancelled.")


def setup_dpg():
    dpg.create_context()

    with dpg.file_dialog(directory_selector=False, show=False, callback=select_template_image_callback, id="template_file_dialog_id", width=500, height=400):
        dpg.add_file_extension(".png,.jpg,.jpeg", color=(0, 255, 0, 255))
        dpg.add_file_extension(".*")

    with dpg.window(label="ESP Configuration", width=500, height=550, tag="main_window"):
        dpg.add_text("Roblox ESP Config (Educational Purposes Only!)")
        dpg.add_checkbox(label="Enable ESP", callback=toggle_esp, tag="enable_esp_checkbox")
        dpg.add_input_text(label="Target Window Title", default_value=config["target_window_title"], callback=set_target_window)
        
        dpg.add_separator()
        dpg.add_text("Offsets:")
        dpg.add_slider_int(label="Offset X", default_value=0, min_value=-200, max_value=200, callback=set_offset_x)
        dpg.add_slider_int(label="Offset Y", default_value=0, min_value=-200, max_value=200, callback=set_offset_y)

        dpg.add_separator()
        dpg.add_text("Color ESP Settings:")
        dpg.add_checkbox(label="Enable Color ESP", default_value=config["color_esp_enabled"], callback=toggle_color_esp)
        
        # Color Picker and manual RGB input
        initial_color_float = [c/255.0 for c in config["target_color_rgb"]] + [1.0]
        dpg.add_text("Target Color:")
        with dpg.group(horizontal=True):
            dpg.add_color_picker(default_value=initial_color_float, label="", callback=set_target_color, no_inputs=True, width=100)
            # Small rectangle to show selected color
            with dpg.drawlist(width=50, height=30): # Adjust size as needed
                 dpg.draw_rectangle((0,0), (50,30), color=initial_color_float[:3]+[1.0], fill=initial_color_float[:3]+[1.0], tag="color_preview_rect_color")


        dpg.add_slider_int(label="Tolerance", default_value=config["tolerance"], min_value=0, max_value=255, callback=set_tolerance)

        dpg.add_separator()
        dpg.add_text("Image ESP Settings (Template Matching):")
        dpg.add_checkbox(label="Enable Image ESP", default_value=config["image_esp_enabled"], callback=toggle_image_esp)
        dpg.add_button(label="Select Template Image", callback=lambda: dpg.show_item("template_file_dialog_id"))
        dpg.add_text("Template: None", tag="template_path_text")
        dpg.add_slider_float(label="Match Threshold", default_value=config["template_threshold"], min_value=0.1, max_value=1.0, format="%.2f", callback=set_template_threshold)
        
        dpg.add_separator()
        dpg.add_text("Status:", tag="status_label")
        dpg.add_text("Not Initialized", tag="status_text", wrap=480)

    dpg.create_viewport(title='ESP Config', width=520, height=600)
    dpg.setup_dearpygui()
    dpg.show_viewport()


# --- Main Application Logic ---
def overlay_update_loop():
    """Dedicated function for updating the GDI overlay."""
    if not overlay_instance.create_window():
        dpg_set_status("FATAL: Overlay window creation failed. Ensure script has permissions or try restarting.")
        stop_event.set() # Signal other threads to stop
        return

    # Simple message pump for the overlay window (optional if UpdateLayeredWindow is primary)
    # But good practice to have one for WM_DESTROY etc.
    msg = ctypes.wintypes.MSG()
    pMsg = ctypes.byref(msg)

    while not stop_event.is_set() and overlay_instance.overlay_active:
        # Check for window messages for the overlay
        if user32.PeekMessageW(pMsg, overlay_instance.hwnd, 0, 0, win32con.PM_REMOVE):
            if msg.message == win32con.WM_QUIT:
                print("Overlay received WM_QUIT.")
                break # Exit loop if WM_QUIT is received
            user32.TranslateMessage(pMsg)
            user32.DispatchMessageW(pMsg)
        else:
            # If ESP is enabled and there are entities, update the frame
            if config["esp_enabled"] and detected_entities:
                overlay_instance.update_frame(detected_entities)
            elif config["esp_enabled"] and not detected_entities: # ESP on but nothing to draw
                 overlay_instance.update_frame([]) # Clear the overlay
            elif not config["esp_enabled"] and overlay_instance.overlay_active: # ESP off, clear overlay
                overlay_instance.update_frame([]) 
            
            time.sleep(0.016) # Cap overlay updates roughly to 60FPS

    overlay_instance.cleanup()
    print("Overlay update loop stopped.")


if __name__ == "__main__":
    print("Starting ESP application...")
    print("Ensure the target game (e.g., Roblox) is running in windowed or borderless windowed mode for best results.")
    print("Run this script as Administrator if overlays don't appear correctly.")

    # Start the ESP worker thread
    esp_thread = threading.Thread(target=esp_worker, daemon=True)
    esp_thread.start()
    print("ESP worker thread started.")

    # Start the Overlay thread (which includes its own window creation and message pump)
    overlay_thread = threading.Thread(target=overlay_update_loop, daemon=True)
    overlay_thread.start()
    print("Overlay thread started.")

    # Setup and run Dear PyGUI
    setup_dpg()
    dpg_set_status("ESP Initialized. Configure and enable.")

    try:
        while dpg.is_dearpygui_running():
            # Custom logic can be run here if needed, but DPG render call is primary
            dpg.render_dearpygui_frame()
            # Add a small sleep if DPG loop is too CPU intensive on its own,
            # but usually DPG handles this well.
            # time.sleep(0.001) 
    except KeyboardInterrupt:
        print("Keyboard interrupt received, shutting down...")
    finally:
        print("Shutting down...")
        stop_event.set() # Signal threads to stop

        if dpg.is_dearpygui_running():
            dpg.stop_dearpygui()
            dpg.destroy_context()
        
        print("Waiting for ESP worker to stop...")
        esp_thread.join(timeout=2)
        print("Waiting for Overlay to stop...")
        overlay_thread.join(timeout=2) # Overlay needs to clean up its window

        print("Application finished.")