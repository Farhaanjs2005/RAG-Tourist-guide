import getpass
from .db import init
from .main import Registration, create_user

if __name__ == '__main__':
    init()
    email = input('Admin email: ')
    name = input('Admin name: ')
    password = getpass.getpass('Password (at least 10 characters): ')
    create_user(Registration(email=email, name=name, password=password), role='admin')
    print('Admin created. You can now sign in.')
