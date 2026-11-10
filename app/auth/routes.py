from flask import render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, logout_user, current_user
from urllib.parse import urlparse
from app import db
from app.models import User
from app.auth import bp
from app.auth.forms import LoginForm, RegistrationForm, ForgotPasswordForm, ResetPasswordForm
from app.notifications import send_password_reset_email


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user is None or not user.check_password(form.password.data):
            flash('Invalid username or password', 'error')
            return redirect(url_for('auth.login'))
        if not user.is_active:
            flash('Your account has been deactivated', 'error')
            return redirect(url_for('auth.login'))

        login_user(user, remember=form.remember_me.data)
        next_page = request.args.get('next')
        if not next_page or urlparse(next_page).netloc != '':
            next_page = url_for('main.index')
        return redirect(next_page)

    return render_template('auth/login.html', title='Sign In', form=form)


@bp.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('auth.login'))


@bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    form = RegistrationForm()
    if form.validate_on_submit():
        user = User(username=form.username.data, email=form.email.data)
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash('Congratulations, you are now registered!', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/register.html', title='Register', form=form)

@bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    form = ForgotPasswordForm()
    if form.validate_on_submit():
        user = User.query.filter(db.func.lower(User.email) == form.email.data.strip().lower()).first()
        if user and user.is_active:
            token = user.get_reset_password_token()
            reset_url = url_for('auth.reset_password', token=token, _external=True)
            try:
                send_password_reset_email(user, reset_url)
            except Exception as e:
                current_app.logger.error(f'Password reset email to user {user.id} failed: {e}')
        # same answer either way, so the form can't be used to find out who has an account
        flash('If that email belongs to an account, a reset link is on its way.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/forgot_password.html', title='Forgot Password', form=form)


@bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    user = User.verify_reset_password_token(token)
    if user is None:
        flash('That reset link is invalid or has expired. Please request a new one.', 'error')
        return redirect(url_for('auth.forgot_password'))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        db.session.commit()
        flash('Your password has been reset. You can now sign in.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/reset_password.html', title='Reset Password', form=form)
