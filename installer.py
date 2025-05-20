try:
    import os

    os.system('python -m pip install pyimgui[full] pyautogui pillow')

except ImportError as e:
    print("error >> " + e)