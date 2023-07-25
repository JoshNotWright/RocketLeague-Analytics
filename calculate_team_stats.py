import pandas as pd
import numpy as np
from scipy.stats import percentileofscore
from war_calculation.offensive_war import calculate_average_offensive_war, calculate_avg_offensive_stats
from war_calculation.defensive_war import calculate_average_defensive_war, calculate_avg_defensive_stats
from utils.replay_downloader import download_player_replays
from war_calculation.calculate_war import calculate_war
import os
from utils.populate_player_cards import *
from utils.get_pro_player_info import get_pro_player_info

def calculate_team_stats(team):
    offensive_war_sum = 0
    defensive_war_sum = 0
    team_war_sum = 0
    avg_goals_sum = 0
    avg_assists_sum = 0
    avg_shot_acc_sum = 0
    avg_boost_sum = 0
    demos_inflicted_sum = 0
    shots_sum = 0
    saves_sum = 0
    shots_conceded_sum = 0

    team_name, *players = [player.strip() for player in team.split(";")]
    players = [p.strip() for player in players for p in player.split(",")]

    for player in players:
        player = player.strip()

        offensive_war = 0
        defensive_war = 0
        player_war = 0
        avg_goals = 0
        avg_assists = 0
        avg_shot_acc = 0
        avg_avg_boost = 0
        avg_demos_inflicted = 0
        avg_shots = 0
        avg_saves = 0
        avg_shots_conceded = 0

        # Perform calculations for each player and add to the sums
        with open("player_offensive_war.txt", "r") as f:
            lines = f.readlines()[0:]  # skip the header line
            for line in lines:
                if line.lower().startswith(player.lower()):
                    _, stats = line.strip().split(":")
                    offensive_war, num_games = stats.split(",")
                    break
            else:
                print("Player not found in the offensive WAR stats file.")

        with open("player_defensive_war.txt", "r") as f:
            lines = f.readlines()[0:]  # skip the header line
            for line in lines:
                if line.lower().startswith(player.lower()):
                    _, stats = line.strip().split(":")
                    defensive_war, num_games = stats.split(",")
                    break
            else:
                print("Player not found in the defensive WAR stats file.")

        with open("player_war.txt", "r") as f:
            lines = f.readlines()[0:]  # skip the header line
            for line in lines:
                if line.lower().startswith(player.lower()):
                    _, stats = line.strip().split(":")
                    player_war, num_games = stats.split(",")
                    break
            else:
                print("Player not found in the overall WAR stats file.")

        with open("player_offensive_stats.txt", "r") as f:
            lines = f.readlines()[0:]  # skip the header line
            for line in lines:
                if line.lower().startswith(player.lower()):
                    _, avg_goals, avg_assists, avg_shot_acc, avg_avg_boost, avg_demos_inflicted, avg_shots, num_games = line.strip().split(";")
                    break
            else:
                print("Player not found in the offensive stats file.")

        with open("player_defensive_stats.txt", "r") as f:
            lines = f.readlines()[0:]  # skip the header line
            for line in lines:
                if line.lower().startswith(player.lower()):
                    _, avg_saves, avg_shots_conceded, num_games = line.strip().split(";")
                    break
            else:
                print("Player not found in the defensive stats file.")

        # Example calculations for each player's stats:
        offensive_war_sum += float(offensive_war)
        defensive_war_sum += float(defensive_war)
        team_war_sum += float(player_war)
        avg_goals_sum += float(avg_goals)
        avg_assists_sum += float(avg_assists)
        avg_shot_acc_sum += float(avg_shot_acc)
        avg_boost_sum += float(avg_avg_boost)
        demos_inflicted_sum += float(avg_demos_inflicted)
        shots_sum += float(avg_shots)
        saves_sum += float(avg_saves)
        shots_conceded_sum += float(avg_shots_conceded)

    # Calculate the averaged shots_conceded value and shot accuracy
    shots_conceded_avg = round(shots_conceded_sum / len(players), 2)
    avg_shot_acc_avg = round(avg_shot_acc_sum / len(players), 2)

    # Write the team stats to the file
    with open("team_stats.txt", "a") as f:
        f.write(
            f"{team_name}: {round(offensive_war_sum, 2)}; {round(defensive_war_sum, 2)}; {round(team_war_sum, 2)}; {round(avg_goals_sum, 2)}; {round(avg_assists_sum, 2)}; {avg_shot_acc_avg}; {round(avg_boost_sum, 2)}; {round(demos_inflicted_sum, 2)}; {round(shots_sum, 2)}; {round(saves_sum, 2)}; {shots_conceded_avg}\n"
        )

# Read the teams from unique_teams.txt and calculate their stats
with open("unique_teams.txt", "r") as f:
    teams = f.readlines()

for team in teams:
    calculate_team_stats(team)
