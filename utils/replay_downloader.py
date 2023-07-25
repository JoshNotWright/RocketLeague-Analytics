import os
import requests
import utils.get_replay_ids as get_replay_ids
import time
from utils.match_id import get_match_id

def download_player_replays(player, count):
    base_url = "https://ballchasing.com/dl/stats/players/"
    replay_ids = get_replay_ids.get_replay_ids(player, "ranked-doubles", count)

    # create replays folder if it doesn't exist
    if not os.path.exists("replays"):
        os.makedirs("replays")

    # download files
    last_msg_length = 0
    for i, replay_id in enumerate(replay_ids):
        url = base_url + replay_id + "/" + replay_id + "-players.csv"
        response = requests.get(url)
        num_attempts = 1
        while response.status_code != 200 and num_attempts <= 5:
            msg = f"Failed to download replay with ID {replay_id}. Retrying in {num_attempts} seconds..."
            if len(msg) < last_msg_length:
                msg += " " * (last_msg_length - len(msg))
            last_msg_length = len(msg)
            print(msg, end="\r")
            time.sleep(num_attempts)
            response = requests.get(url)
            num_attempts += 1
        if response.status_code == 200:
            replay_filename = f"{replay_id}.csv"
            with open(f"replays/{replay_filename}", "wb") as f:
                f.write(response.content)
            msg = f"Downloaded replay {i+1}/{count} ({int(((i+1)/count)*100)}%) for {player} with ID {replay_id}"
            if len(msg) < last_msg_length:
                msg += " " * (last_msg_length - len(msg))
            last_msg_length = len(msg)
            print(msg, end="\r")
            time.sleep(1.0)
            match_id = get_match_id(replay_filename)
            match_filename = f"{match_id}.csv"
            match_path = os.path.join("replays", match_filename)
            replay_path = os.path.join("replays", replay_filename)
            if os.path.exists(match_path):
                os.remove(match_path)
            os.rename(replay_path, match_path)
        else:
            msg = f"Failed to download replay with ID {replay_id}"
            if len(msg) < last_msg_length:
                msg += " " * (last_msg_length - len(msg))
            last_msg_length = len(msg)
            print(msg, end="\r")
            time.sleep(10.0)
    print(f"All replays downloaded for {player}")
