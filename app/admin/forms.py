from flask_wtf import FlaskForm
from wtforms import StringField, BooleanField, SubmitField, PasswordField, SelectMultipleField, TextAreaField, SelectField
from wtforms.validators import DataRequired, Optional, Email, EqualTo, ValidationError
from app.models import User, SourceType


class PublicationForm(FlaskForm):
    name = StringField('Publication Name', validators=[DataRequired()])
    publication_domain = StringField('Publication Domain', validators=[DataRequired()])
    industry_description = TextAreaField('Industry Description', validators=[Optional()])
    reader_personas = TextAreaField('Target Reader Personas', validators=[Optional()])
    reader_pain_points = TextAreaField('Reader Pain Points & Needs', validators=[Optional()])
    access_api_key = StringField('Access API Key', validators=[Optional()])
    cms_url = StringField('CMS URL', validators=[Optional()])
    cms_api_key = StringField('CMS API Key', validators=[Optional()])
    is_active = BooleanField('Active')
    submit = SubmitField('Save')


class NewsSourceForm(FlaskForm):
    name = StringField('Source Name', validators=[DataRequired()])
    source_type = SelectField('Source Type', choices=SourceType.choices(), validators=[Optional()])
    url = StringField('URL', validators=[Optional()])
    keywords = TextAreaField('Keywords', validators=[Optional()])
    is_active = BooleanField('Active')
    submit = SubmitField('Save')


class UserForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password')
    password2 = PasswordField('Confirm Password', validators=[EqualTo('password', message='Passwords must match')])
    roles = SelectMultipleField('Roles', coerce=int)
    publications = SelectMultipleField('Publications', coerce=int)
    is_active = BooleanField('Active', default=True)
    submit = SubmitField('Save')

    def __init__(self, original_username=None, original_email=None, *args, **kwargs):
        super(UserForm, self).__init__(*args, **kwargs)
        self.original_username = original_username
        self.original_email = original_email

    def validate_username(self, username):
        if username.data != self.original_username:
            user = User.query.filter_by(username=username.data).first()
            if user is not None:
                raise ValidationError('Username already exists.')

    def validate_email(self, email):
        if email.data != self.original_email:
            user = User.query.filter_by(email=email.data).first()
            if user is not None:
                raise ValidationError('Email already exists.')