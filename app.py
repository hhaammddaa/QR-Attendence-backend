from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import qrcode
import sqlite3
from werkzeug.security import safe_join
import logging
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
class Config:
    DATABASE_PATH = os.getenv('DATABASE_PATH', 'attendance.db')
    QR_CODES_DIR = os.getenv('QR_CODES_DIR', 'qrcodes')
    SECRET_KEY = os.getenv('SECRET_KEY', 'your-secret-key-here')
    ALLOWED_ORIGINS = os.getenv('ALLOWED_ORIGINS', '*').split(',')
    MAX_REQUESTS_PER_MINUTE = int(os.getenv('MAX_REQUESTS_PER_MINUTE', 60))

# Initialize Flask app
app = Flask(__name__)
app.config.from_object(Config)

# Configure CORS with specific origins
CORS(app, origins=Config.ALLOWED_ORIGINS, supports_credentials=True)

# Configure rate limiting
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=[f"{Config.MAX_REQUESTS_PER_MINUTE} per minute"]
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class DatabaseError(Exception):
    """Custom exception for database-related errors"""
    pass

class QRCodeError(Exception):
    """Custom exception for QR code-related errors"""
    pass

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def get_connection(self) -> sqlite3.Connection:
        """Create and return a database connection"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error as e:
            logger.error(f"Database connection error: {e}")
            raise DatabaseError("Failed to connect to database")

    def init_db(self) -> None:
        """Initialize database tables"""
        try:
            with self.get_connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS attendance (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT NOT NULL,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                # Create index for faster queries
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_user_id_timestamp 
                    ON attendance(user_id, timestamp)
                """)
        except sqlite3.Error as e:
            logger.error(f"Database initialization error: {e}")
            raise DatabaseError("Failed to initialize database")

class AttendanceService:
    def __init__(self, database: Database):
        self.database = database

    def mark_attendance(self, user_id: str) -> None:
        """Mark attendance for a user"""
        try:
            with self.database.get_connection() as conn:
                conn.execute(
                    "INSERT INTO attendance (user_id) VALUES (?)",
                    (user_id,)
                )
        except sqlite3.Error as e:
            logger.error(f"Error marking attendance: {e}")
            raise DatabaseError("Failed to mark attendance")

    def get_attendance_records(self) -> List[Dict[str, Any]]:
        """Retrieve all attendance records"""
        try:
            with self.database.get_connection() as conn:
                cursor = conn.execute(
                    "SELECT * FROM attendance ORDER BY timestamp DESC"
                )
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Error retrieving attendance records: {e}")
            raise DatabaseError("Failed to retrieve attendance records")

class QRCodeService:
    def __init__(self, qr_codes_dir: str):
        self.qr_codes_dir = Path(qr_codes_dir)
        self.qr_codes_dir.mkdir(exist_ok=True)

    def generate_qr_code(self, user_id: str) -> str:
        """Generate QR code for a user"""
        try:
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr.add_data(user_id)
            qr.make(fit=True)

            qr_image = qr.make_image(fill_color="black", back_color="white")
            qr_path = safe_join(str(self.qr_codes_dir), f"{user_id}.png")
            
            if qr_path:
                qr_image.save(qr_path)
                return qr_path
            raise QRCodeError("Invalid QR code path")
            
        except Exception as e:
            logger.error(f"Error generating QR code: {e}")
            raise QRCodeError("Failed to generate QR code")

# Initialize services
database = Database(Config.DATABASE_PATH)
database.init_db()
attendance_service = AttendanceService(database)
qr_code_service = QRCodeService(Config.QR_CODES_DIR)

def validate_user_id(user_id: Optional[str]) -> bool:
    """Validate user ID format"""
    return bool(user_id and user_id.strip() and len(user_id) <= 50)

@app.route('/api/generate_qr', methods=['POST'])
@limiter.limit(f"{Config.MAX_REQUESTS_PER_MINUTE}/minute")
def generate_qr() -> Response:
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        user_id = data.get("user_id")
        if not validate_user_id(user_id):
            return jsonify({"error": "Invalid user ID"}), 400

        qr_path = qr_code_service.generate_qr_code(user_id)
        return jsonify({
            "message": "QR Code generated successfully",
            "qr_path": qr_path
        })

    except QRCodeError as e:
        logger.error(f"QR code generation failed: {e}")
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route('/api/mark_attendance', methods=['POST'])
@limiter.limit(f"{Config.MAX_REQUESTS_PER_MINUTE}/minute")
def mark_attendance() -> Response:
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        user_id = data.get("user_id")
        if not validate_user_id(user_id):
            return jsonify({"error": "Invalid user ID"}), 400

        attendance_service.mark_attendance(user_id)
        return jsonify({"message": "Attendance marked successfully"})

    except DatabaseError as e:
        logger.error(f"Database error: {e}")
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route('/api/get_attendance', methods=['GET'])
@limiter.limit(f"{Config.MAX_REQUESTS_PER_MINUTE}/minute")
def get_attendance() -> Response:
    try:
        records = attendance_service.get_attendance_records()
        return jsonify(records)
    except DatabaseError as e:
        logger.error(f"Database error: {e}")
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.errorhandler(429)
def ratelimit_handler(e: Any) -> Response:
    return jsonify({"error": "Rate limit exceeded"}), 429

if __name__ == "__main__":
    app.run(debug=os.getenv('FLASK_DEBUG', 'False').lower() == 'true')