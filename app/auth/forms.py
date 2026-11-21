from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Length


class LoginForm(FlaskForm):
    identifier = StringField(
        'Email or username',
        validators=[DataRequired(), Length(max=100)],
    )
    password = PasswordField(
        'Password',
        validators=[DataRequired(), Length(max=72)],
    )
    remember = BooleanField('Remember me')
    submit = SubmitField('Sign in')
