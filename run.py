from app import create_app, db
from app.models import User, Role, Publication, NewsSource, NewsContent

app = create_app()


@app.shell_context_processor
def make_shell_context():
    return {
        'db': db,
        'User': User,
        'Role': Role,
        'Publication': Publication,
        'NewsSource': NewsSource,
        'NewsContent': NewsContent
    }


if __name__ == '__main__':
    app.run(debug=True)