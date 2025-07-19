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
import re
import hashlib

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
    
    async def ensure_worksheet_exists(self, client, headers, sheet_name: str):
        """Ensure the worksheet exists and has the correct headers."""
        try:
            # Extract spreadsheet ID from URL
            spreadsheet_id = self.spreadsheet_url.split("/d/")[1].split("/")[0]
            spreadsheet = await client.open_by_key(spreadsheet_id)
            
            # Check if worksheet exists
            try:
                worksheet = await spreadsheet.worksheet(sheet_name)
            except gspread.exceptions.WorksheetNotFound:
                # Create worksheet if it doesn't exist
                worksheet = await spreadsheet.add_worksheet(
                    title=sheet_name, 
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
        worksheet = await self.ensure_worksheet_exists(client, headers, self.sheet_name)
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

    async def add_survey_response(self, response_data):
        """
        Add survey response to Google Sheets.
        
        response_data should be a dictionary with:
        - timestamp: datetime object
        - student_username: string
        - course_name: string
        - group_name: string
        - survey_title: string
        - question_text: string
        - question_type: string
        - answer: string
        """
        client = await self.get_client()
        if not client:
            logger.error("Could not get Google Sheets client for survey response")
            return False
        
        raw_title = response_data.get("survey_title", "survey")
        safe_title = re.sub(r"[\\\/\?\*\[\]\:]", "_", raw_title)[:100]
        sheet_name = safe_title or "survey"
        headers = [
            "Timestamp", 
            "Username", 
            "Course", 
            "Group", 
            "Survey Title", 
            "Question", 
            "Question Type", 
            "Answer",
            "Session ID"
        ]
        
        worksheet = await self.ensure_worksheet_exists(client, headers, sheet_name)
        if not worksheet:
            logger.error("Could not get or create survey responses worksheet")
            return False
        
        # Format the timestamp
        timestamp = response_data.get("timestamp", datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
        
        # Prepare row data
        row = [
            timestamp,
            response_data.get("student_username", ""),
            response_data.get("course_name", ""),
            response_data.get("group_name", ""),
            response_data.get("survey_title", ""),
            response_data.get("question_text", ""),
            response_data.get("question_type", ""),
            response_data.get("answer", ""),
            response_data.get("session_id", "")
        ]
        
        try:
            # Append the row to the worksheet
            await worksheet.append_row(row)

            col1 = await worksheet.col_values(1)
            new_row = len(col1)

            session_id = response_data.get("session_id","")
            color = self._get_color_for_session(session_id)
            spreadsheet_id = self.spreadsheet_url.split("/d/")[1].split("/")[0]
            spreadsheet = await client.open_by_key(spreadsheet_id)
            await self._color_row(
                spreadsheet,
                worksheet,
                row_index=new_row,
                color=color,
                num_columns=len(headers)
            )
            return True
        except Exception as e:
            logger.error(f"Error adding survey response to Google Sheets: {e}")
            return False 

    def _get_color_for_session(self, session_id: str) -> dict:
        palette = [
            {"red":0.98, "green":0.94, "blue":0.96},  # бледно-розовый
            {"red":0.94, "green":0.98, "blue":0.94},  # бледно-зелёный
            {"red":0.94, "green":0.96, "blue":0.98},  # бледно-голубой
            {"red":0.96, "green":0.94, "blue":0.98},  # бледно-лиловый
            {"red":0.98, "green":0.96, "blue":0.94},  # бледно-персиковый
            {"red":0.96, "green":0.98, "blue":0.94},  # бледно-мятный
            {"red":0.94, "green":0.98, "blue":0.96},  # бледно-аквамариновый
            {"red":0.96, "green":0.94, "blue":0.96},  # бледно-лилово-розовый
        ]
        h = int(hashlib.sha256(session_id.encode()).hexdigest(), 16)
        return palette[h % len(palette)]

    async def _color_row(self, spreadsheet, worksheet, row_index: int, color: dict, num_columns: int):
        sheet_id = worksheet.id
        requests = [{
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": row_index - 1,
                    "endRowIndex": row_index,
                    "startColumnIndex": 0,
                    "endColumnIndex": num_columns
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": color
                    }
                },
                "fields": "userEnteredFormat.backgroundColor"
            }
        }]
        await spreadsheet.batch_update({"requests": requests})