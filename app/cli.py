import click
from app import db
from app.models import User, Role


def register_commands(app):
    @app.cli.command()
    @click.option('--username', default='admin', help='Admin username')
    @click.option('--email', default='admin@example.com', help='Admin email')
    @click.option('--password', default='admin123', help='Admin password')
    def create_admin(username, email, password):
        """Create an admin user."""
        # Create admin role if it doesn't exist
        admin_role = Role.query.filter_by(name='admin').first()
        if not admin_role:
            admin_role = Role(name='admin', description='Administrator with full access')
            db.session.add(admin_role)
            click.echo('Created admin role')

        # Create editor role if it doesn't exist
        editor_role = Role.query.filter_by(name='editor').first()
        if not editor_role:
            editor_role = Role(name='editor', description='Editor can manage content')
            db.session.add(editor_role)
            click.echo('Created editor role')

        db.session.commit()

        # Check if user already exists
        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            click.echo(f'User "{username}" already exists!')
            return

        # Create admin user
        admin_user = User(username=username, email=email)
        admin_user.set_password(password)
        admin_user.roles.append(admin_role)

        db.session.add(admin_user)
        db.session.commit()

        click.echo(f'Admin user created successfully!')
        click.echo(f'Username: {username}')
        click.echo(f'Email: {email}')
        click.echo(f'Password: {password}')
        click.echo('\nPlease change the password after first login!')

    @app.cli.command()
    def init_db():
        """Initialize the database with roles."""
        # Create admin role
        admin_role = Role.query.filter_by(name='admin').first()
        if not admin_role:
            admin_role = Role(name='admin', description='Administrator with full access')
            db.session.add(admin_role)
            click.echo('Created admin role')

        # Create editor role
        editor_role = Role.query.filter_by(name='editor').first()
        if not editor_role:
            editor_role = Role(name='editor', description='Editor can manage content')
            db.session.add(editor_role)
            click.echo('Created editor role')

        db.session.commit()
        click.echo('Database initialized with roles!')