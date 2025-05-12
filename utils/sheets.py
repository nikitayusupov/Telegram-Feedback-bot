"""
utils/sheets.py
~~~~~~~~~~~~~~~~~~~~~~

Utility to interact with Google Sheets API for storing feedback.
"""

import gspread
import gspread_asyncio
from google.oauth2.service_account import Credentials
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

def get_creds(service_account_file):
    """Get credentials for Google Sheets API from service account file."""
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    
    try:
        creds = Credentials.from_service_account_file(
            service_account_file, 
            scopes=scopes
        )
        return creds
    except Exception as e:
        logger.error(f"Error creating credentials from service account file: {e}")
        return None

class GoogleSheetsManager:
    """Manages interactions with Google Sheets for feedback storage."""
    
    def __init__(self, creds_path, spreadsheet_url, sheet_name="feedback"):
        self.creds_path = creds_path
        self.spreadsheet_url = spreadsheet_url
        self.sheet_name = sheet_name
        self.agcm = gspread_asyncio.AsyncioGspreadClientManager(
            lambda: get_creds(self.creds_path)
        )
        
    async def get_client(self):
        """Get an authenticated Google Sheets client."""
        try:
            return await self.agcm.authorize()
        except Exception as e:
            logger.error(f"Error authenticating with Google Sheets: {e}")
            return None
    
    async def ensure_worksheet_exists(self, client, headers):
        """Ensure the worksheet exists and has the correct headers."""
        try:
            # Extract spreadsheet ID from URL
            spreadsheet_id = self.spreadsheet_url.split("/d/")[1].split("/")[0]
            spreadsheet = await client.open_by_key(spreadsheet_id)
            
            # Check if worksheet exists
            try:
                worksheet = await spreadsheet.worksheet(self.sheet_name)
            except gspread.exceptions.WorksheetNotFound:
                # Create worksheet if it doesn't exist
                worksheet = await spreadsheet.add_worksheet(
                    title=self.sheet_name, 
                    rows=1, 
                    cols=len(headers)
                )
                # Set headers
                await worksheet.append_row(headers)
                
            return worksheet
        except Exception as e:
            logger.error(f"Error ensuring worksheet exists: {e}")
            return None
    
    async def add_feedback(self, feedback_data):
        """
        Add feedback to Google Sheets.
        
        feedback_data should be a dictionary with:
        - timestamp: datetime object
        - student_username: string
        - course_name: string
        - topic: string
        - text: string
        """
        client = await self.get_client()
        if not client:
            logger.error("Could not get Google Sheets client")
            return False
        
        headers = ["Timestamp", "Username", "Course", "Topic", "Feedback"]
        worksheet = await self.ensure_worksheet_exists(client, headers)
        if not worksheet:
            logger.error("Could not get or create worksheet")
            return False
            
        # Format the timestamp
        timestamp = feedback_data.get("timestamp", datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
        
        # Prepare row data
        row = [
            timestamp,
            feedback_data.get("student_username", ""),
            feedback_data.get("course_name", ""),
            feedback_data.get("topic", ""),
            feedback_data.get("text", "")
        ]
        
        try:
            # Append the row to the worksheet
            await worksheet.append_row(row)
            return True
        except Exception as e:
            logger.error(f"Error adding feedback to Google Sheets: {e}")
            return False 