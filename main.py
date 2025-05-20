import tkinter as tk
from tkinter import ttk, colorchooser, filedialog
import mss
import numpy as np
from PIL import Image, ImageTk # ImageTk for displaying images in Tkinter if needed (not used for template preview here)
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

# --- Overlay Window Class (Unchanged from original) ---
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

        self.hwnd = win32gui.CreateWindowEx(
            WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_NOACTIVATE,
            class_atom, "GDIOverlayWindow", win32con.WS_POPUP,
            0, 0, self.screen_width, self.screen_height,
            None, None, wc.hInstance, None
        )
        if not self.hwnd:
            print(f"Failed to create overlay window. Error: {win32api.GetLastError()}")
            return False
        win32gui.SetLayeredWindowAttributes(self.hwnd, 0, 255, win32con.LWA_ALPHA)
        win32gui.ShowWindow(self.hwnd, win32con.SW_SHOW)
        win32gui.UpdateWindow(self.hwnd)
        win32gui.SetWindowPos(self.hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                              win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
        hdc_screen = user32.GetDC(None)
        self.hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        self.hbm_mem = gdi32.CreateCompatibleBitmap(hdc_screen, self.screen_width, self.screen_height)
        self.old_hbm = gdi32.SelectObject(self.hdc_mem, self.hbm_mem)
        user32.ReleaseDC(None, hdc_screen)
        logBrush = win32gui.LOGBRUSH()
        logBrush.lbStyle = win32con.BS_HOLLOW
        self.hbrush_box = gdi32.CreateBrushIndirect(logBrush)
        self.hfont = gdi32.GetStockObject(win32con.DEFAULT_GUI_FONT)
        self.overlay_active = True
        print("Overlay window created successfully.")
        return True

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == win32con.WM_DESTROY:
            self.cleanup()
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def update_target_window(self, window_title):
        if not window_title: # Explicitly clear target
            self.target_game_hwnd = None
            self.game_rect = (0,0,0,0)
            return False
        
        new_target_hwnd = win32gui.FindWindow(None, window_title)
        if new_target_hwnd:
            self.target_game_hwnd = new_target_hwnd
            self.game_rect = win32gui.GetWindowRect(self.target_game_hwnd)
            return True
        else:
            # Only clear if the title was valid but window not found now
            # This prevents clearing if an empty title is passed due to transient UI states
            if self.target_game_hwnd and window_title == win32gui.GetWindowText(self.target_game_hwnd):
                 self.target_game_hwnd = None
                 self.game_rect = (0,0,0,0)
            return False


    def update_frame(self, entities_to_draw):
        if not self.overlay_active or not self.hwnd or not self.hdc_mem:
            return

        gdi32.SelectObject(self.hdc_mem, self.hbm_mem)
        hbrush_clear = gdi32.CreateSolidBrush(win32api.RGB(0,0,0))
        rect_clear = (0, 0, self.screen_width, self.screen_height)
        user32.FillRect(self.hdc_mem, ctypes.byref(ctypes.wintypes.RECT(*rect_clear)), hbrush_clear)
        gdi32.DeleteObject(hbrush_clear)

        old_font = gdi32.SelectObject(self.hdc_mem, self.hfont)
        old_brush = gdi32.SelectObject(self.hdc_mem, self.hbrush_box)
        gdi32.SetBkMode(self.hdc_mem, win32con.TRANSPARENT)

        for x, y, w, h, label, box_color_tuple in entities_to_draw:
            if x + w > self.screen_width or y + h > self.screen_height or x < 0 or y < 0:
                continue
            r, g, b = box_color_tuple
            pen_color = win32api.RGB(r, g, b)
            hpen = gdi32.CreatePen(win32con.PS_SOLID, 1, pen_color)
            old_pen = gdi32.SelectObject(self.hdc_mem, hpen)
            gdi32.Rectangle(self.hdc_mem, x, y, x + w, y + h)
            gdi32.SelectObject(self.hdc_mem, old_pen)
            gdi32.DeleteObject(hpen)
            if label:
                text_r, text_g, text_b = config["draw_text_color_rgb"]
                gdi32.SetTextColor(self.hdc_mem, win32api.RGB(text_r, text_g, text_b))
                gdi32.TextOutW(self.hdc_mem, x, y - 15, label, len(label))
        
        gdi32.SelectObject(self.hdc_mem, old_font)
        gdi32.SelectObject(self.hdc_mem, old_brush)

        source_pos = ctypes.wintypes.POINT(0, 0)
        screen_pos = ctypes.wintypes.POINT(0, 0)
        size = ctypes.wintypes.SIZE(self.screen_width, self.screen_height)
        blend_op = win32con.AC_SRC_OVER
        
        success = user32.UpdateLayeredWindow(
            self.hwnd, None, ctypes.byref(screen_pos), ctypes.byref(size),
            self.hdc_mem, ctypes.byref(source_pos), 0,
            ctypes.byref(ctypes.c_ulong(win32con.AC_SRC_ALPHA | blend_op)),
            win32con.ULW_ALPHA
        )
        if not success:
             print(f"UpdateLayeredWindow failed: {win32api.GetLastError()}")

    def cleanup(self):
        self.overlay_active = False
        if self.hbrush_box: gdi32.DeleteObject(self.hbrush_box); self.hbrush_box = None
        if self.hdc_mem:
            if self.old_hbm: gdi32.SelectObject(self.hdc_mem, self.old_hbm)
            if self.hbm_mem: gdi32.DeleteObject(self.hbm_mem); self.hbm_mem = None
            gdi32.DeleteDC(self.hdc_mem); self.hdc_mem = None
        if self.hwnd: win32gui.DestroyWindow(self.hwnd); self.hwnd = None
        print("Overlay cleaned up.")

overlay_instance = GDIOverlay()
stop_event = threading.Event()

# --- Tkinter specific globals ---
root_tk = None
status_var_tk = None
color_preview_tk = None
template_path_var_tk = None

def tk_set_status(message):
    if status_var_tk and root_tk:
        try:
            # Ensure update happens on the main Tkinter thread
            root_tk.after(0, lambda: status_var_tk.set(message))
        except tk.TclError: # If root window is destroyed
            print(f"Status (Tkinter not ready/destroyed): {message}")
    else:
        print(f"Status (Tkinter not ready): {message}")


# --- ESP Logic Thread ---
def esp_worker():
    global detected_entities, config
    sct = mss.mss()
    template = None
    template_w, template_h = 0, 0
    last_target_title_check = "" # To avoid spamming "window not found"

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
            draw_box_clr = tuple(config["draw_box_color_rgb"])

        if not esp_on:
            if detected_entities: # Clear only once when ESP is turned off
                detected_entities = []
            time.sleep(0.1)
            continue

        # Find the target window and get its dimensions
        # Check if overlay_instance's target needs update
        current_overlay_target_text = ""
        if overlay_instance.target_game_hwnd:
            try: # Window might close between check and GetWindowText
                current_overlay_target_text = win32gui.GetWindowText(overlay_instance.target_game_hwnd)
            except:
                 overlay_instance.target_game_hwnd = None # Window gone

        if not overlay_instance.target_game_hwnd or current_overlay_target_text != target_title:
            if not overlay_instance.update_target_window(target_title):
                if target_title and target_title != last_target_title_check: # Print only on change or if title exists
                    tk_set_status(f"Target window '{target_title}' not found.")
                    last_target_title_check = target_title
                detected_entities = []
                time.sleep(1)
                continue
            else: # Window found
                tk_set_status(f"Tracking: {target_title}")
                last_target_title_check = target_title # Reset checker on success

        if not overlay_instance.target_game_hwnd: # Still no window after trying to update
            time.sleep(1)
            continue

        game_rect = overlay_instance.game_rect # Use rect from overlay_instance
        # Check if window is minimized or invalid
        if game_rect[0] == -32000 or game_rect[1] == -32000 or (game_rect[2] - game_rect[0]) <= 0 or (game_rect[3] - game_rect[1]) <= 0:
            if target_title != last_target_title_check or "minimized" not in status_var_tk.get(): # Avoid spam
                tk_set_status(f"{target_title} is minimized or invalid size.")
                last_target_title_check = target_title
            detected_entities = []
            time.sleep(0.5)
            continue

        monitor = {"top": game_rect[1], "left": game_rect[0],
                   "width": game_rect[2] - game_rect[0],
                   "height": game_rect[3] - game_rect[1]}

        img_pil = sct.grab(monitor)
        img_np = np.array(img_pil, dtype=np.uint8)[:, :, :3]

        current_detected = []

        if color_esp_on:
            lower_bound = np.clip(target_rgb - tolerance_val, 0, 255)
            upper_bound = np.clip(target_rgb + tolerance_val, 0, 255) # Corrected clip

            mask = cv2.inRange(img_np, lower_bound, upper_bound)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                if cv2.contourArea(contour) > 50:
                    x, y, w, h = cv2.boundingRect(contour)
                    screen_x = game_rect[0] + x + offset_x_val
                    screen_y = game_rect[1] + y + offset_y_val
                    current_detected.append((screen_x, screen_y, w, h, "Color", draw_box_clr))

        if image_esp_on and template_path:
            if template is None or config["template_image_path"] != getattr(esp_worker, "last_template_path", ""):
                try:
                    template_pil = Image.open(template_path).convert('RGB')
                    template = np.array(template_pil, dtype=np.uint8)
                    template_h, template_w = template.shape[:2]
                    esp_worker.last_template_path = template_path
                    tk_set_status(f"Template loaded: {os.path.basename(template_path)}")
                except Exception as e:
                    tk_set_status(f"Error loading template: {e}")
                    template = None

            if template is not None and template_w > 0 and template_h > 0:
                res = cv2.matchTemplate(img_np, template, cv2.TM_CCOEFF_NORMED)
                loc = np.where(res >= template_thresh)
                processed_rects = []
                min_dist_sq = (template_w * 0.5)**2 + (template_h * 0.5)**2
                for pt in zip(*loc[::-1]):
                    is_overlapping = False
                    for pr_x, pr_y, pr_w, pr_h in processed_rects:
                        center_x, center_y = pt[0] + template_w//2, pt[1] + template_h//2
                        pr_center_x, pr_center_y = pr_x + pr_w//2, pr_y + pr_h//2
                        dist_sq = (center_x - pr_center_x)**2 + (center_y - pr_center_y)**2
                        if dist_sq < min_dist_sq:
                            is_overlapping = True; break
                    if is_overlapping: continue
                    processed_rects.append((pt[0], pt[1], template_w, template_h))
                    screen_x = game_rect[0] + pt[0] + offset_x_val
                    screen_y = game_rect[1] + pt[1] + offset_y_val
                    current_detected.append((screen_x, screen_y, template_w, template_h, "Image", draw_box_clr))

        detected_entities = current_detected
        time.sleep(0.01)

    sct.close()
    print("ESP worker stopped.")

# --- Tkinter Callbacks and Setup ---
def setup_tkinter_gui(app_root):
    global root_tk, status_var_tk, color_preview_tk, template_path_var_tk # Allow modification

    root_tk = app_root # Assign to global for tk_set_status
    root_tk.title("ESP Configuration")
    root_tk.geometry("500x600") # Adjusted height for status bar

    # --- Tkinter Variables ---
    esp_enabled_var = tk.BooleanVar(value=config["esp_enabled"])
    target_window_var = tk.StringVar(value=config["target_window_title"])
    offset_x_var = tk.IntVar(value=config["offset_x"])
    offset_y_var = tk.IntVar(value=config["offset_y"])
    color_esp_enabled_var = tk.BooleanVar(value=config["color_esp_enabled"])
    # target_color_rgb is handled by a button and preview
    tolerance_var = tk.IntVar(value=config["tolerance"])
    image_esp_enabled_var = tk.BooleanVar(value=config["image_esp_enabled"])
    template_path_var_tk = tk.StringVar(value=f"Template: {os.path.basename(config['template_image_path']) if config['template_image_path'] else 'None'}")
    template_threshold_var = tk.DoubleVar(value=config["template_threshold"])
    status_var_tk = tk.StringVar(value="Not Initialized")


    # --- Callback Functions ---
    def toggle_esp():
        with config_lock:
            config["esp_enabled"] = esp_enabled_var.get()
        tk_set_status(f"ESP {'Enabled' if esp_enabled_var.get() else 'Disabled'}")

    def set_target_window(*args): # *args for trace
        new_title = target_window_var.get()
        with config_lock:
            config["target_window_title"] = new_title
        # Attempt to update overlay's target immediately, esp_worker will also do this
        # overlay_instance.update_target_window(new_title) # this might spam if typed char by char
        # Let esp_worker handle the primary update based on config.
        # Or add a button "Apply Target Window"
        tk_set_status(f"Target window set to: {new_title}")


    def set_offset_x(value): # Scale passes value directly
        with config_lock:
            config["offset_x"] = int(float(value))

    def set_offset_y(value):
        with config_lock:
            config["offset_y"] = int(float(value))

    def toggle_color_esp():
        with config_lock:
            config["color_esp_enabled"] = color_esp_enabled_var.get()

    def select_target_color():
        # Initial color for chooser could be config["target_color_rgb"]
        initial_hex = '#%02x%02x%02x' % tuple(config["target_color_rgb"])
        color_code = colorchooser.askcolor(title="Choose target color", initialcolor=initial_hex)
        if color_code and color_code[0]: # color_code is ((r,g,b), "#rrggbb")
            rgb_int = [int(c) for c in color_code[0]]
            with config_lock:
                config["target_color_rgb"] = rgb_int
            if color_preview_tk:
                color_preview_tk.config(bg=color_code[1]) # color_code[1] is hex string

    def set_tolerance(value):
        with config_lock:
            config["tolerance"] = int(float(value))

    def toggle_image_esp():
        with config_lock:
            config["image_esp_enabled"] = image_esp_enabled_var.get()

    def select_template_image():
        file_path = filedialog.askopenfilename(
            title="Select Template Image",
            filetypes=(("Image files", "*.png *.jpg *.jpeg"), ("All files", "*.*"))
        )
        if file_path:
            with config_lock:
                config["template_image_path"] = file_path
            template_path_var_tk.set(f"Template: {os.path.basename(file_path)}")
            # Force esp_worker to reload template by resetting its last_template_path cache
            if hasattr(esp_worker, "last_template_path"):
                esp_worker.last_template_path = ""
        else:
            tk_set_status("No template image selected.")


    def set_template_threshold(value):
        with config_lock:
            config["template_threshold"] = float(value)

    # --- UI Layout ---
    main_frame = ttk.Frame(root_tk, padding="10")
    main_frame.pack(expand=True, fill="both")

    ttk.Label(main_frame, text="Roblox ESP Config (Educational Purposes Only!)", font=("Arial", 12, "bold")).pack(pady=(0,10))
    
    # General ESP Settings
    ttk.Checkbutton(main_frame, text="Enable ESP", variable=esp_enabled_var, command=toggle_esp).pack(anchor="w")
    
    target_frame = ttk.Frame(main_frame)
    target_frame.pack(fill="x", pady=5)
    ttk.Label(target_frame, text="Target Window Title:").pack(side="left", padx=(0,5))
    entry_target_window = ttk.Entry(target_frame, textvariable=target_window_var)
    entry_target_window.pack(side="left", expand=True, fill="x")
    # Add a small delay or use a button if live update is too much
    target_window_var.trace_add("write", set_target_window)


    ttk.Separator(main_frame, orient="horizontal").pack(fill="x", pady=10)
    
    # Offsets
    ttk.Label(main_frame, text="Offsets:").pack(anchor="w")
    offset_frame = ttk.Frame(main_frame)
    offset_frame.pack(fill="x", pady=5)
    ttk.Label(offset_frame, text="Offset X:").pack(side="left")
    ttk.Scale(offset_frame, from_=-200, to=200, variable=offset_x_var, orient="horizontal", command=set_offset_x).pack(side="left", expand=True, fill="x", padx=5)
    
    offset_y_frame = ttk.Frame(main_frame) # New frame for Y to align properly
    offset_y_frame.pack(fill="x", pady=5)
    ttk.Label(offset_y_frame, text="Offset Y:").pack(side="left")
    ttk.Scale(offset_y_frame, from_=-200, to=200, variable=offset_y_var, orient="horizontal", command=set_offset_y).pack(side="left", expand=True, fill="x", padx=5)


    ttk.Separator(main_frame, orient="horizontal").pack(fill="x", pady=10)

    # Color ESP
    ttk.Label(main_frame, text="Color ESP Settings:", font=("Arial", 10, "bold")).pack(anchor="w")
    ttk.Checkbutton(main_frame, text="Enable Color ESP", variable=color_esp_enabled_var, command=toggle_color_esp).pack(anchor="w")
    
    color_frame = ttk.Frame(main_frame)
    color_frame.pack(fill="x", pady=5)
    ttk.Button(color_frame, text="Target Color", command=select_target_color).pack(side="left", padx=(0,10))
    color_preview_tk = tk.Frame(color_frame, width=50, height=20, relief="sunken", borderwidth=1)
    color_preview_tk.pack(side="left")
    initial_hex_preview = '#%02x%02x%02x' % tuple(config["target_color_rgb"])
    color_preview_tk.config(bg=initial_hex_preview)


    tol_frame = ttk.Frame(main_frame)
    tol_frame.pack(fill="x", pady=5)
    ttk.Label(tol_frame, text="Tolerance:").pack(side="left")
    ttk.Scale(tol_frame, from_=0, to=255, variable=tolerance_var, orient="horizontal", command=set_tolerance).pack(side="left", expand=True, fill="x", padx=5)

    ttk.Separator(main_frame, orient="horizontal").pack(fill="x", pady=10)

    # Image ESP
    ttk.Label(main_frame, text="Image ESP Settings:", font=("Arial", 10, "bold")).pack(anchor="w")
    ttk.Checkbutton(main_frame, text="Enable Image ESP", variable=image_esp_enabled_var, command=toggle_image_esp).pack(anchor="w")
    ttk.Button(main_frame, text="Select Template Image", command=select_template_image).pack(anchor="w", pady=5)
    ttk.Label(main_frame, textvariable=template_path_var_tk).pack(anchor="w")

    thresh_frame = ttk.Frame(main_frame)
    thresh_frame.pack(fill="x", pady=5)
    ttk.Label(thresh_frame, text="Match Threshold:").pack(side="left")
    ttk.Scale(thresh_frame, from_=0.1, to=1.0, variable=template_threshold_var, orient="horizontal", command=set_template_threshold).pack(side="left", expand=True, fill="x", padx=5)
    # For float scale, you might want to format the display if you add a label showing the value
    # For now, the scale itself is the indicator.

    ttk.Separator(main_frame, orient="horizontal").pack(fill="x", pady=10)
    
    # Status Bar (at the bottom of the root window)
    status_bar = ttk.Frame(root_tk, relief="sunken", padding=2)
    status_bar.pack(side="bottom", fill="x")
    ttk.Label(status_bar, textvariable=status_var_tk, anchor="w").pack(fill="x")


# --- Main Application Logic ---
def overlay_update_loop():
    if not overlay_instance.create_window():
        tk_set_status("FATAL: Overlay window creation failed. Check permissions.")
        stop_event.set()
        return

    msg = ctypes.wintypes.MSG()
    pMsg = ctypes.byref(msg)

    while not stop_event.is_set() and overlay_instance.overlay_active:
        if user32.PeekMessageW(pMsg, overlay_instance.hwnd, 0, 0, win32con.PM_REMOVE):
            if msg.message == win32con.WM_QUIT:
                print("Overlay received WM_QUIT.")
                break
            user32.TranslateMessage(pMsg)
            user32.DispatchMessageW(pMsg)
        else:
            with config_lock: # Read config safely
                esp_is_enabled = config["esp_enabled"]
            
            # Create a local copy of entities to draw to avoid issues if it's modified mid-update
            # This needs to be protected by a lock if `detected_entities` can be written by another thread
            # while this thread is reading it. For now, assuming `esp_worker` updates it atomically.
            # A deepcopy might be safer if entities are complex: import copy; entities_copy = copy.deepcopy(detected_entities)
            entities_copy = list(detected_entities) # Shallow copy is fine for list of tuples

            if esp_is_enabled:
                 overlay_instance.update_frame(entities_copy)
            elif overlay_instance.overlay_active: # ESP off but overlay active, clear it
                overlay_instance.update_frame([])
            
            time.sleep(0.016) # ~60 FPS

    overlay_instance.cleanup()
    print("Overlay update loop stopped.")


if __name__ == "__main__":
    print("Starting ESP application...")
    print("Ensure the target game is running in windowed or borderless windowed mode.")
    print("Run as Administrator if overlays don't appear correctly.")

    # Create Tkinter root window and global vars BEFORE starting threads that might use them
    _root_tk_instance = tk.Tk() # This will be assigned to global `root_tk` in setup_tkinter_gui

    esp_thread = threading.Thread(target=esp_worker, daemon=True)
    overlay_thread = threading.Thread(target=overlay_update_loop, daemon=True)

    # This function populates _root_tk_instance and sets up callbacks
    setup_tkinter_gui(_root_tk_instance) # Pass the instance

    def on_closing_tk():
        print("Tkinter window closing, shutting down...")
        stop_event.set()
        # Give threads a moment to see the stop_event
        # The join logic will happen after mainloop exits
        if root_tk: # Check if root_tk was successfully initialized
            root_tk.destroy() # This will cause mainloop to exit

    if root_tk: # Ensure setup_tkinter_gui was successful
        root_tk.protocol("WM_DELETE_WINDOW", on_closing_tk)

    # Start threads after GUI is mostly set up
    esp_thread.start()
    print("ESP worker thread started.")
    overlay_thread.start()
    print("Overlay thread started.")

    tk_set_status("ESP Initialized. Configure and enable.")

    try:
        if root_tk:
            root_tk.mainloop()
    except KeyboardInterrupt:
        print("Keyboard interrupt received, shutting down...")
        stop_event.set() # Ensure stop_event is set if mainloop interrupted
        if root_tk and root_tk.winfo_exists(): # Check if window still exists
            root_tk.destroy()

    # Mainloop has exited, now clean up
    print("Shutting down threads...")
    if esp_thread.is_alive():
        print("Waiting for ESP worker to stop...")
        esp_thread.join(timeout=3)
    if overlay_thread.is_alive():
        print("Waiting for Overlay to stop...")
        overlay_thread.join(timeout=3)

    # Check if threads are still alive after timeout
    if esp_thread.is_alive():
        print("Warning: ESP worker thread did not stop gracefully.")
    if overlay_thread.is_alive():
        print("Warning: Overlay thread did not stop gracefully.")


    print("Application finished.")