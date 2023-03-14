import utils.get_unique_players as get_unique_players
import time
from utils.replay_downloader import download_player_replays
from war_calculation.offensive_war import *
from war_calculation.defensive_war import *
from utils.get_replay_ids import get_replay_ids
from war_calculation.calculate_war import calculate_war
from utils.remove_duplicates import *
from utils.scale_war import scale_war

players = get_unique_players.get_unique_players()

# Download 25 replays for each player
count = 25
num_players = len(players)

# Flag variable to indicate if downloads should be stopped
stop_downloads = False

for i, player in enumerate(players):
    replay_ids = get_replay_ids(player, "ranked-doubles", count)
    if len(replay_ids) >= count:
        try:
            download_player_replays(player, count)
            time.sleep(5.0)
        except KeyboardInterrupt:
            print("\nDownloads cancelled by user. Processing data then exiting...")
            stop_downloads = True
    else:
        print(f"{player} does not have enough replays available.")

    # If downloads should be stopped, skip to the next step
    if stop_downloads:
        break

    # Calculate and display progress
    progress = (i + 1) / num_players
    print(f"Progress: {progress:.0%}")

# Calculate average O WAR and total games for each player and save to text file
print("\nCalculating Offensive WAR...")
with open("player_offensive_war.txt", "a") as f:
    num_players = len(players)
    for i, player in enumerate(players):
        avg_war, num_games = calculate_average_offensive_war(player)
        if num_games >= 15:
            f.write(f"{player}: {avg_war:.2f}, {num_games}\n")
        else:
            continue
        # Print progress
        progress = (i + 1) / num_players * 100
        print(f"\rProgress: {progress:.2f}%", end="")

print(f"\rProgress: Complete", end="")
#remove_duplicates_war('player_offensive_war.txt')

# Calculate average D WAR and total games for each player and save to text file
print("\nCalculating Defensive WAR...")
with open("player_defensive_war.txt", "a") as f:
    num_players = len(players)
    for i, player in enumerate(players):
        avg_war, num_games = calculate_average_defensive_war(player)
        if num_games >= 15:
            f.write(f"{player}: {avg_war:.2f}, {num_games}\n")
        else:
            continue
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
        if float(num_games) >= 15.0:
            offensive_war_dict[player_name.strip()] = float(offensive_war)

with open("player_defensive_war.txt", "r") as f:
    for line in f:
        if line.count(":") > 1:
            player_name, stats = line.strip().rsplit(":", 1)
        else:
            player_name, stats = line.strip().split(":")
        defensive_war, num_games = stats.split(",")
        if float(num_games) >= 15.0:
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
        if num_games >= 15:
            f.write(f"{player};{avg_goals:.2f};{avg_assists:.2f};{avg_shot_acc:.2f};{avg_boost:.2f};{avg_demos_inflicted:.2f};{avg_shots};{num_games}\n")
        else:
            continue
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
        if num_games >= 15:
            f.write(f"{player};{avg_saves:.2f};{avg_shots_conceded:.2f};{num_games}\n")
        else:
            continue
        # Print progress
        progress = (i + 1) / num_players * 100
        print(f"\rProgress: {progress:.2f}%", end="")

print(f"\rProgress: Complete", end="")
remove_duplicates_stats('player_defensive_stats.txt')



