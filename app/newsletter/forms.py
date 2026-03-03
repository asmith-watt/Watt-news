from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, BooleanField, IntegerField, SelectField, SubmitField
from wtforms.validators import DataRequired, Optional, NumberRange


class NewsletterTemplateForm(FlaskForm):
    name = StringField('Template Name', validators=[DataRequired()])
    header_html = TextAreaField('Header HTML', validators=[Optional()])
    footer_html = TextAreaField('Footer HTML', validators=[Optional()])
    primary_color = StringField('Primary Color', default='#1a2b3c', validators=[DataRequired()])
    secondary_color = StringField('Secondary Color', default='#f5f5f5', validators=[DataRequired()])
    include_intro = BooleanField('Include Intro Paragraph')
    max_articles = IntegerField('Max Articles', default=10, validators=[DataRequired(), NumberRange(min=1, max=50)])
    is_active = BooleanField('Active', default=True)
    submit = SubmitField('Save')
