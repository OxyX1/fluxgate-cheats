import os


while True:
    user_input = input("enter package you want to install >>> ")

    os.system('python -m pip install ' + user_input)