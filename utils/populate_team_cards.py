import gspread
from google.oauth2 import credentials
from google_auth_oauthlib.flow import InstalledAppFlow
import os
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

def update_cell_with_percentage(cell_range, new_value):
    # Convert the number to a percentage rounded to 2 decimal places
    new_value = round(new_value * 0.01, 2)

    # Path to the credentials JSON file
    credentials_path = 'credentials.json'
    token_path = 'token.json'  # Path to store the generated token

    # Load the credentials or authenticate if token file doesn't exist
    creds = None
    if os.path.exists(token_path):
        creds = credentials.Credentials.from_authorized_user_file(token_path, scopes=['https://www.googleapis.com/auth/spreadsheets'])
    else:
        flow = InstalledAppFlow.from_client_secrets_file(credentials_path, scopes=['https://www.googleapis.com/auth/spreadsheets'])
        creds = flow.run_local_server(port=0)
        # Save the credentials to a file for future use
        with open(token_path, 'w') as token_file:
            token_file.write(creds.to_json())

    # Create a client instance for Google Sheets
    client = gspread.authorize(creds)

    # Open the Google Sheets document by its key
    spreadsheet_key = '12Q_fPgsFS_tTRa1r5HcNhOxSXRoF3dkOBioHrUvMTk4'
    sheet = client.open_by_key(spreadsheet_key)

    # Select a specific worksheet within the Google Sheets document
    worksheet = sheet.worksheet('Team Card')

    # Build the Sheets API service
    service = build('sheets', 'v4', credentials=creds)

    # Define the update request
    values = [[new_value]]  # Use the new value passed as argument

    # Create the request body for updating the cell
    request_body = {
        'values': values,
        'range': f'{worksheet.title}!{cell_range}',
        'majorDimension': 'ROWS'
    }

    # Create the request to update the cell value
    request = service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_key,
        range=f'{worksheet.title}!{cell_range}',
        valueInputOption='USER_ENTERED',
        body=request_body
    )

    try:
        # Execute the request to update the cell value
        response = request.execute()

        # Format the cell as a percentage
        cell_format = {
            'numberFormat': {
                'type': 'PERCENT',
                'pattern': '0%'
            }
        }

        # Create the request body for formatting the cell
        format_body = {
            'requests': [
                {
                    'repeatCell': {
                        'range': {
                            'sheetId': worksheet.id,
                            'startRowIndex': int(cell_range.split(':')[0][1:])-1,
                            'endRowIndex': int(cell_range.split(':')[1][1:]),
                            'startColumnIndex': ord(cell_range.split(':')[0][0]) - ord('A'),
                            'endColumnIndex': ord(cell_range.split(':')[1][0]) - ord('A') + 1
                        },
                        'cell': {'userEnteredFormat': cell_format},
                        'fields': 'userEnteredFormat.numberFormat'
                    }
                }
            ]
        }

        # Create the request to format the cell
        format_request = service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_key,
            body=format_body
        )

        # Execute the request to format the cell
        format_response = format_request.execute()

    except HttpError as e:
        print('Error updating cell value:', e)

def update_cell_with_text(cell_range, new_text):
    # Path to the credentials JSON file
    credentials_path = 'credentials.json'
    token_path = 'token.json'  # Path to store the generated token

    # Load the credentials or authenticate if token file doesn't exist
    creds = None
    if os.path.exists(token_path):
        creds = credentials.Credentials.from_authorized_user_file(token_path, scopes=['https://www.googleapis.com/auth/spreadsheets'])
    else:
        flow = InstalledAppFlow.from_client_secrets_file(credentials_path, scopes=['https://www.googleapis.com/auth/spreadsheets'])
        creds = flow.run_local_server(port=0)
        # Save the credentials to a file for future use
        with open(token_path, 'w') as token_file:
            token_file.write(creds.to_json())

    # Create a client instance for Google Sheets
    client = gspread.authorize(creds)

    # Open the Google Sheets document by its key
    spreadsheet_key = '12Q_fPgsFS_tTRa1r5HcNhOxSXRoF3dkOBioHrUvMTk4'
    sheet = client.open_by_key(spreadsheet_key)

    # Select a specific worksheet within the Google Sheets document
    worksheet = sheet.worksheet('Team Card')

    # Build the Sheets API service
    service = build('sheets', 'v4', credentials=creds)

    # Define the update request
    values = [[new_text]]  # Use the new text passed as argument

    # Create the request body for updating the cell
    request_body = {
        'values': values,
        'range': f'{worksheet.title}!{cell_range}',
        'majorDimension': 'ROWS'
    }

    # Create the request to update the cell value
    request = service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_key,
        range=f'{worksheet.title}!{cell_range}',
        valueInputOption='USER_ENTERED',
        body=request_body
    )

    try:
        # Execute the request to update the cell value
        response = request.execute()

    except HttpError as e:
        print('Error updating cell value:', e)

def update_cell_with_image(cell_range, image_url):
    # Path to the credentials JSON file
    credentials_path = 'credentials.json'
    token_path = 'token.json'  # Path to store the generated token

    # Load the credentials or authenticate if token file doesn't exist
    creds = None
    if os.path.exists(token_path):
        creds = credentials.Credentials.from_authorized_user_file(token_path, scopes=['https://www.googleapis.com/auth/spreadsheets'])
    else:
        flow = InstalledAppFlow.from_client_secrets_file(credentials_path, scopes=['https://www.googleapis.com/auth/spreadsheets'])
        creds = flow.run_local_server(port=0)
        # Save the credentials to a file for future use
        with open(token_path, 'w') as token_file:
            token_file.write(creds.to_json())

    # Create a client instance for Google Sheets
    client = gspread.authorize(creds)

    # Open the Google Sheets document by its key
    spreadsheet_key = '12Q_fPgsFS_tTRa1r5HcNhOxSXRoF3dkOBioHrUvMTk4'
    sheet = client.open_by_key(spreadsheet_key)

    # Select a specific worksheet within the Google Sheets document
    worksheet = sheet.worksheet('Team Card')

    # Build the Sheets API service
    service = build('sheets', 'v4', credentials=creds)

    # Define the update request
    values = [[f'=IMAGE("{image_url}")']]  # Use the image URL passed as argument

    # Create the request body for updating the cell
    request_body = {
        'values': values,
        'range': f'{worksheet.title}!{cell_range}',
        'majorDimension': 'ROWS'
    }

    # Create the request to update the cell value
    request = service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_key,
        range=f'{worksheet.title}!{cell_range}',
        valueInputOption='USER_ENTERED',
        body=request_body
    )

    try:
        # Execute the request to update the cell value
        response = request.execute()

    except HttpError as e:
        print('Error updating cell with image:', e)


def update_cell_with_number(cell_range, new_number):
    # Path to the credentials JSON file
    credentials_path = 'credentials.json'
    token_path = 'token.json'  # Path to store the generated token

    # Load the credentials or authenticate if token file doesn't exist
    creds = None
    if os.path.exists(token_path):
        creds = credentials.Credentials.from_authorized_user_file(token_path, scopes=['https://www.googleapis.com/auth/spreadsheets'])
    else:
        flow = InstalledAppFlow.from_client_secrets_file(credentials_path, scopes=['https://www.googleapis.com/auth/spreadsheets'])
        creds = flow.run_local_server(port=0)
        # Save the credentials to a file for future use
        with open(token_path, 'w') as token_file:
            token_file.write(creds.to_json())

    # Create a client instance for Google Sheets
    client = gspread.authorize(creds)

    # Open the Google Sheets document by its key
    spreadsheet_key = '12Q_fPgsFS_tTRa1r5HcNhOxSXRoF3dkOBioHrUvMTk4'
    sheet = client.open_by_key(spreadsheet_key)

    # Select a specific worksheet within the Google Sheets document
    worksheet = sheet.worksheet('Team Card')

    # Build the Sheets API service
    service = build('sheets', 'v4', credentials=creds)

    # Define the update request
    values = [[new_number]]  # Use the new number passed as an argument

    # Create the request body for updating the cell
    request_body = {
        'values': values,
        'range': f'{worksheet.title}!{cell_range}',
        'majorDimension': 'ROWS'
    }

    # Create the request to update the cell value
    request = service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_key,
        range=f'{worksheet.title}!{cell_range}',
        valueInputOption='USER_ENTERED',
        body=request_body
    )

    try:
        # Execute the request to update the cell value
        response = request.execute()

    except HttpError as e:
        print('Error updating cell value:', e)