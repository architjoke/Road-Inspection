from flask import Flask
from flask_wtf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv
import os

load_dotenv()

csrf = CSRFProtect()
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100 per hour", "20 per minute"],
    storage_uri="memory://"  # Fixes the warning
)

def create_app():
    app = Flask(__name__)
    
    # Config
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-key')
    app.config['DATABASE_URL'] = os.getenv('DATABASE_URL')
    
    # Initialize extensions
    csrf.init_app(app)
    limiter.init_app(app)
    
    # Register routes
    from app.routes import main_bp
    app.register_blueprint(main_bp)
    
    return app
