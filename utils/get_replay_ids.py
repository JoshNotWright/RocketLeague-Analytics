import requests
import json
import time

def get_replay_ids(player_name, playlist, count):

    url = "https://ballchasing.com/api/replays"

    # Query parameters
    params = {
        "player-name": player_name,
        "playlist": playlist,
        "count": count,
        "sort-by": "replay-date"
    }

    # Headers
    headers = {
        "Authorization": "XEw4Q7oHqauAV47tU0eiwRzq6JSBqyOJB4aKb1zA"
    }

    # Make the request
    response = requests.get(url, params=params, headers=headers)

    # Check if the request was successful
    if response.status_code == 200:
        # Convert response to JSON format
        response_json = json.loads(response.text)

        # Get replay IDs
        replay_ids = [replay["id"] for replay in response_json["list"]]

        # Print the replay IDs
        return replay_ids
    elif response.status_code == 429:
        # If we get a 429 error, wait for a specified amount of time before retrying the request
        retry_after = int(response.headers.get("Retry-After"))
        print(f"Error {response.status_code}: {response.text}. Retrying in {retry_after} seconds...")
        time.sleep(retry_after)
        return get_replay_ids(player_name, playlist, count)
    else:
        # Print the error status code and message
        print(f"Error {response.status_code}: {response.text}")
