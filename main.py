import imgui
from imgui.integrations.glfw import GlfwRenderer
import glfw
import pyautogui
from PIL import Image
import ctypes

# Globals
selected_color = (255, 0, 0)
offset_x = 0
offset_y = 0

def find_and_move_to_color():
    screenshot = pyautogui.screenshot()
    width, height = screenshot.size
    pixels = screenshot.load()

    target_rgb = selected_color

    for x in range(0, width, 2):  # scan every 2 pixels for speed
        for y in range(0, height, 2):
            if pixels[x, y][:3] == target_rgb:
                pyautogui.moveTo(x + offset_x, y + offset_y)
                return True
    return False

def rgba_to_float(r, g, b):
    return r / 255.0, g / 255.0, b / 255.0, 1.0

def float_to_rgb(color_tuple):
    return tuple(int(c * 255) for c in color_tuple[:3])

def main():
    global selected_color, offset_x, offset_y

    if not glfw.init():
        print("Could not initialize GLFW")
        return

    window = glfw.create_window(400, 200, "ImGui Color Finder", None, None)
    glfw.make_context_current(window)
    impl = GlfwRenderer(window)

    color_float = rgba_to_float(*selected_color)

    while not glfw.window_should_close(window):
        glfw.poll_events()
        impl.process_inputs()

        imgui.new_frame()

        imgui.begin("Color Tracker")

        changed, color_float = imgui.color_edit3("Target Color", *color_float)
        if changed:
            selected_color = float_to_rgb(color_float)

        changed_x, offset_x = imgui.input_int("Offset X", offset_x)
        changed_y, offset_y = imgui.input_int("Offset Y", offset_y)

        if imgui.button("Find Color and Move Mouse"):
            success = find_and_move_to_color()
            if not success:
                print("Color not found on screen.")

        imgui.end()
        imgui.render()
        impl.render(imgui.get_draw_data())
        glfw.swap_buffers(window)

    impl.shutdown()
    glfw.terminate()

if __name__ == "__main__":
    main()
