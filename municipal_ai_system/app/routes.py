from flask import Blueprint, jsonify, render_template

main_bp = Blueprint('main', __name__)

@main_bp.route('/health')
def health_check():
    return jsonify({"status": "ok", "message": "API is running"})

@main_bp.route('/')
def home():
    return render_template('base.html')