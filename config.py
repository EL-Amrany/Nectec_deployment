import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev_secret_hpc_app')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///hpc_app.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', 'key')  # <-- Insert your key or set as env variable
