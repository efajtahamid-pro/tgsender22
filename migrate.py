from app import create_app
from app.extensions import db

def reset_database():
    """Drops all existing tables and recreates them with the latest schema."""
    app = create_app()
    with app.app_context():
        print("⏳ Dropping existing database tables...")
        db.drop_all()
        print("⏳ Creating new database tables with latest schema...")
        db.create_all()
        print("✅ Database schema synchronized successfully!")

if __name__ == '__main__':
    reset_database()
