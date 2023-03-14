import requests
import json

def get_rank_replay_ids(rank, playlist, count):

    url = "https://ballchasing.com/api/replays"

    # Query parameters
    # TODO Add season
    params = {
        "min-rank": rank,
        "max-rank": rank,
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
        return(replay_ids)
    else:
        # Print the error status code and message
        print(f"Error {response.status_code}: {response.text}")