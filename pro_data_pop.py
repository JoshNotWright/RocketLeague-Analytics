import requests
import json
import time
import os
import csv
import time
from war_calculation.offensive_war import *
from war_calculation.defensive_war import *
from war_calculation.calculate_war import calculate_war
from utils.remove_duplicates import *
from utils.scale_war import scale_war
import os

def get_group_children(main_group_id):
    url = "https://ballchasing.com/api/groups"

    # Query parameters
    params = {
        "group": main_group_id,
        "count": "200",
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

        # Get group IDs
        group_ids = [group["id"] for group in response_json["list"]]

        # If there are no child groups, call get_replay_ids function
        if len(group_ids) == 0:
            replay_ids = get_replay_ids(main_group_id)
            return replay_ids

        # Recursively get replay IDs from child groups
        replay_ids = []
        for group_id in group_ids:
            replay_ids.extend(get_group_children(group_id))

        # Return the replay IDs
        return replay_ids
    else:
        # Print the error status code and message
        print(f"Error {response.status_code}: {response.text}")

def get_replay_ids(group):

    url = "https://ballchasing.com/api/replays"

    # Query parameters
    params = {
        "group": group,
        "count": 200,
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
    else:
        # Print the error status code and message
        print(f"Error {response.status_code}: {response.text}")

def download_group_replays(main_group_id):
    base_url = "https://ballchasing.com/dl/stats/players/"
    replay_ids = get_group_children(main_group_id)
    count = len(replay_ids)

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
            msg = f"Downloaded replay {i+1}/{count} ({int(((i+1)/count)*100)}%) with ID {replay_id}"
            if len(msg) < last_msg_length:
                msg += " " * (last_msg_length - len(msg))
            last_msg_length = len(msg)
            print(msg, end="\r")
            time.sleep(1.0)
        else:
            msg = f"Failed to download replay with ID {replay_id}"
            if len(msg) < last_msg_length:
                msg += " " * (last_msg_length - len(msg))
            last_msg_length = len(msg)
            print(msg, end="\r")
            time.sleep(10.0)
    print(f"All replays downloaded")

def get_unique_players():
    player_set = set()

    for file_name in os.listdir('replays'):
        if file_name.endswith('.csv'):
            with open(os.path.join('replays', file_name), newline='') as csvfile:
                reader = csv.reader(csvfile, delimiter=';', quotechar='|')
                next(reader)  # skip header row
                for row in reader:
                    player_set.add(row[2])

    player_list = sorted(list(player_set))
    return player_list

# create replays folder if it doesn't exist
if not os.path.exists("replays"):
    os.makedirs("replays")

# Get all replay IDs from the nested groups 
#download_group_replays("bds-vs-vit-bracket-reset-m5k2ahum1v")
players = get_unique_players()

# Calculate average O WAR and total games for each player and save to text file
print("\nCalculating Offensive WAR...")
with open("player_offensive_war.txt", "a") as f:
    num_players = len(players)
    for i, player in enumerate(players):
        avg_war, num_games = calculate_average_offensive_war(player)
        f.write(f"{player}: {avg_war:.2f}, {num_games}\n")
        # Print progress
        progress = (i + 1) / num_players * 100
        print(f"\rProgress: {progress:.2f}%", end="")

print(f"\rProgress: Complete", end="")
remove_duplicates_war('player_offensive_war.txt')

# Calculate average D WAR and total games for each player and save to text file
print("\nCalculating Defensive WAR...")
with open("player_defensive_war.txt", "a") as f:
    num_players = len(players)
    for i, player in enumerate(players):
        avg_war, num_games = calculate_average_defensive_war(player)
        f.write(f"{player}: {avg_war:.2f}, {num_games}\n")
        # Print progress
        progress = (i + 1) / num_players * 100
        print(f"\rProgress: {progress:.2f}%", end="")

print(f"\rProgress: Complete", end="")
remove_duplicates_war('player_defensive_war.txt')

# Calculate player WAR and save to text file
print("\nCalculating Player WAR...")

# Create dictionary to store offensive and defensive WAR for each player
offensive_war_dict = {}
defensive_war_dict = {}

# Read in offensive and defensive WAR for each player from files
with open("player_offensive_war.txt", "r") as f:
    for line in f:
        if line.count(":") > 1:
            player_name, stats = line.strip().rsplit(":", 1)
        else:
            player_name, stats = line.strip().split(":")
        offensive_war, num_games = stats.split(",")
        offensive_war_dict[player_name.strip()] = float(offensive_war)

with open("player_defensive_war.txt", "r") as f:
    for line in f:
        if line.count(":") > 1:
            player_name, stats = line.strip().rsplit(":", 1)
        else:
            player_name, stats = line.strip().split(":")
        defensive_war, num_games = stats.split(",")
        defensive_war_dict[player_name.strip()] = float(defensive_war)

# Calculate player WAR and write to file
with open("player_war.txt", "w") as f:
    num_players = len(offensive_war_dict)
    i = 0
    for player_name in offensive_war_dict:
        if player_name in defensive_war_dict:
            offensive_war = offensive_war_dict[player_name]
            defensive_war = defensive_war_dict[player_name]
            player_war = calculate_war(offensive_war, defensive_war)
            f.write(f"{player_name}: {player_war:.2f}, {num_games}\n")
        else:
            continue
        # Update progress
        i += 1
        progress = i / num_players * 100
        print(f"\rProgress: {progress:.2f}%", end="")

print(f"\rProgress: Complete", end="")

# Calculate average offensive stats and save to text file
print("\nCalculating Offensive Stats...")
with open("player_offensive_stats.txt", "a") as f:
    num_players = len(players)
    for i, player in enumerate(players):
        avg_goals, avg_assists, avg_shot_acc, avg_boost, avg_demos_inflicted, avg_shots, num_games = calculate_avg_offensive_stats(player)
        f.write(f"{player};{avg_goals:.2f};{avg_assists:.2f};{avg_shot_acc:.2f};{avg_boost:.2f};{avg_demos_inflicted:.2f};{avg_shots};{num_games}\n")
        # Print progress
        progress = (i + 1) / num_players * 100
        print(f"\rProgress: {progress:.2f}%", end="")

print(f"\rProgress: Complete", end="")
remove_duplicates_stats('player_offensive_stats.txt')

# Calculate average defensive stats and save to text file
print("\nCalculating Defensive Stats...")
with open("player_defensive_stats.txt", "a") as f:
    num_players = len(players)
    for i, player in enumerate(players):
        avg_saves, avg_shots_conceded, num_games = calculate_avg_defensive_stats(player)
        f.write(f"{player};{avg_saves:.2f};{avg_shots_conceded:.2f};{num_games}\n")
        # Print progress
        progress = (i + 1) / num_players * 100
        print(f"\rProgress: {progress:.2f}%", end="")

print(f"\rProgress: Complete", end="")
remove_duplicates_stats('player_defensive_stats.txt')
