#!/usr/bin/env python
"""
Database initialization script
Creates initial admin user and roles
"""
from app import create_app, db
from app.models import User, Role
from getpass import getpass

app = create_app()

with app.app_context():
    print("Initializing database...")

    # Create tables
    db.create_all()

    # Check if admin role exists
    admin_role = Role.query.filter_by(name='admin').first()
    if not admin_role:
        admin_role = Role(name='admin', description='Administrator with full access')
        db.session.add(admin_role)
        print("Created admin role")

    # Check if editor role exists
    editor_role = Role.query.filter_by(name='editor').first()
    if not editor_role:
        editor_role = Role(name='editor', description='Editor can manage content')
        db.session.add(editor_role)
        print("Created editor role")

    db.session.commit()

    # Check if admin user exists
    admin_user = User.query.filter_by(username='admin').first()
    if not admin_user:
        print("\nCreating admin user...")
        username = input("Admin username (default: admin): ").strip() or "admin"
        email = input("Admin email: ").strip()
        password = getpass("Admin password: ")

        admin_user = User(username=username, email=email)
        admin_user.set_password(password)
        admin_user.roles.append(admin_role)

        db.session.add(admin_user)
        db.session.commit()

        print(f"\nAdmin user '{username}' created successfully!")
    else:
        print("\nAdmin user already exists")

    print("\nDatabase initialization complete!")