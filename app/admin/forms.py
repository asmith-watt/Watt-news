from flask_wtf import FlaskForm
from wtforms import StringField, BooleanField, SubmitField, PasswordField, SelectMultipleField, TextAreaField, SelectField, IntegerField
from wtforms.validators import DataRequired, Optional, Email, EqualTo, ValidationError, NumberRange
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

    # Research fields
    require_candidate_review = BooleanField('Require Candidate Review')

    # Scheduling fields
    schedule_enabled = BooleanField('Enable Scheduled Content Generation')
    schedule_frequency = SelectField('Frequency', choices=[
        ('', '-- Select --'),
        ('daily', 'Daily'),
        ('weekly', 'Weekly')
    ], validators=[Optional()])
    schedule_time = SelectField('Time (UTC)', choices=[
        ('', '-- Select --'),
        ('00:00', '12:00 AM (Midnight)'),
        ('01:00', '1:00 AM'),
        ('02:00', '2:00 AM'),
        ('03:00', '3:00 AM'),
        ('04:00', '4:00 AM'),
        ('05:00', '5:00 AM'),
        ('06:00', '6:00 AM'),
        ('07:00', '7:00 AM'),
        ('08:00', '8:00 AM'),
        ('09:00', '9:00 AM'),
        ('10:00', '10:00 AM'),
        ('11:00', '11:00 AM'),
        ('12:00', '12:00 PM (Noon)'),
        ('13:00', '1:00 PM'),
        ('14:00', '2:00 PM'),
        ('15:00', '3:00 PM'),
        ('16:00', '4:00 PM'),
        ('17:00', '5:00 PM'),
        ('18:00', '6:00 PM'),
        ('19:00', '7:00 PM'),
        ('20:00', '8:00 PM'),
        ('21:00', '9:00 PM'),
        ('22:00', '10:00 PM'),
        ('23:00', '11:00 PM'),
    ], validators=[Optional()])
    schedule_day_of_week = SelectField('Day of Week', choices=[
        ('', '-- Select --'),
        ('0', 'Monday'),
        ('1', 'Tuesday'),
        ('2', 'Wednesday'),
        ('3', 'Thursday'),
        ('4', 'Friday'),
        ('5', 'Saturday'),
        ('6', 'Sunday'),
    ], validators=[Optional()])

    submit = SubmitField('Save')


class NewsSourceForm(FlaskForm):
    name = StringField('Source Name', validators=[DataRequired()])
    source_type = SelectField('Source Type', choices=SourceType.choices(), validators=[Optional()])
    url = StringField('URL', validators=[Optional()])
    keywords = TextAreaField('Keywords', validators=[Optional()])
    config_json = TextAreaField('Configuration JSON', validators=[Optional()])
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