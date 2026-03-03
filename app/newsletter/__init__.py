from flask import Blueprint

bp = Blueprint('newsletter', __name__, url_prefix='/newsletters')

from app.newsletter import routes
