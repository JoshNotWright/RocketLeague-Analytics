import os
import csv
from utils.get_pro_player_info import get_pro_player_info
import time

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


def get_unique_teams():
    players = get_unique_players()
    team_players = {}
    name_corrections = {}

    # Load name corrections from file
    with open('name_corrections.txt', 'r') as file:
        for line in file:
            in_game_name, online_name = line.strip().split(';')
            name_corrections[in_game_name] = online_name

    for player in players:
        if player in name_corrections:
            corrected_player = name_corrections[player]
            team_name, _, _, _ = get_pro_player_info(corrected_player)
        else:
            team_name, _, _, _ = get_pro_player_info(player)
        
        if team_name:
            if team_name not in team_players:
                team_players[team_name] = []
            team_players[team_name].append(player)

    with open('unique_teams.txt', 'w') as file:
        for team, players in team_players.items():
            file.write(f"{team}; {', '.join(players)}\n")

    print("Unique teams stored in unique_teams.txt")


# Run the function to retrieve unique teams and store them in the file
get_unique_teams()