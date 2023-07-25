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

def get_offensive_war_percentile(score):
    with open("player_offensive_war.txt", "r") as f:
        lines = f.readlines()[0:]  # skip the header line
        scores = []
        for line in lines:
            parts = line.strip().rsplit(":", 1)
            player = parts[0].strip()
            offensive_war, num_games = parts[1].split(",")
            scores.append(round(float(offensive_war), 2))
        rounded_score = round(score, 2)
        percentile = round(sum([1 for s in scores if round(s, 2) < rounded_score]) / len(scores) * 100, 2)
        if rounded_score == max(scores):
            percentile = 100.0
        print(f"{rounded_score} is in the {percentile:.2f} percentile in Offensive WAR")
        return percentile


def get_defensive_war_percentile(score):
    with open("player_defensive_war.txt", "r") as f:
        lines = f.readlines()[0:]  # skip the header line
        scores = []
        for line in lines:
            parts = line.strip().rsplit(":", 1)
            player = parts[0].strip()
            defensive_war, num_games = parts[1].split(",")
            scores.append(round(float(defensive_war), 2))
        rounded_score = round(score, 2)
        percentile = round(sum([1 for s in scores if round(s, 2) < rounded_score]) / len(scores) * 100, 2)
        if rounded_score == max(scores):
            percentile = 100.0
        print(f"{rounded_score} is in the {percentile:.2f} percentile in Defensive WAR")
        return percentile

    
def get_war_percentile(score):
    with open("player_war.txt", "r") as f:
        lines = f.readlines()[0:]  # skip the header line
        scores = []
        for line in lines:
            parts = line.strip().rsplit(":", 1)
            player = parts[0].strip()
            player_war, num_games = parts[1].split(",")
            scores.append(float(player_war))
        rounded_score = round(score, 2)
        percentile = round((sum([1 for s in scores if round(s, 2) < rounded_score]) / len(scores)) * 100, 2)
        if rounded_score == max(scores):
            percentile = 100.0
        print(f"{rounded_score} is in the {percentile:.2f} percentile in Overall WAR")
        return percentile

def get_offensive_stats_percentile(avg_goals, avg_assists, avg_shot_acc, avg_avg_boost, avg_demos_inflicted, avg_shots):
    with open("player_offensive_stats.txt", "r") as f:
        lines = f.readlines()[0:]  # skip the header line
        stats = [[float(stat) for stat in line.split(";")[1:]] for line in lines]
        goals_percentile = round(sum([1 for s in stats if round(s[0], 2) <= round(avg_goals, 2)]) / len(stats) * 100, 2)
        assists_percentile = round(sum([1 for s in stats if round(s[1], 2) <= round(avg_assists, 2)]) / len(stats) * 100, 2)
        shot_acc_percentile = round(sum([1 for s in stats if round(s[2], 2) <= round(avg_shot_acc, 2)]) / len(stats) * 100, 2)
        avg_boost_percentile = round(sum([1 for s in stats if round(s[3], 2) <= round(avg_avg_boost, 2)]) / len(stats) * 100, 2)
        demos_inflicted_percentile = round(sum([1 for s in stats if round(s[4], 2) <= round(avg_demos_inflicted, 2)]) / len(stats) * 100, 2)
        shots_percentile = round(sum([1 for s in stats if round(s[5], 2) <= round(avg_shots, 2)]) / len(stats) * 100, 2)
        print(f"Average Goals: {round(avg_goals, 2)} is in the {goals_percentile:.2f} percentile")
        print(f"Average Assists: {round(avg_assists, 2)} is in the {assists_percentile:.2f} percentile")
        print(f"Average Shot Accuracy: {round(avg_shot_acc, 2)} is in the {shot_acc_percentile:.2f} percentile")
        print(f"Average Boost: {round(avg_avg_boost, 2)} is in the {avg_boost_percentile:.2f} percentile")
        print(f"Average Demos Inflicted: {round(avg_demos_inflicted, 2)} is in the {demos_inflicted_percentile:.2f} percentile")
        print(f"Average Shots: {round(avg_shots, 2)} is in the {shots_percentile:.2f} percentile")
        return goals_percentile, assists_percentile, shot_acc_percentile, avg_boost_percentile, demos_inflicted_percentile, shots_percentile


def get_defensive_stats_percentile(avg_saves, avg_shots_conceded):
    with open("player_defensive_stats.txt", "r") as f:
        lines = f.readlines()[0:]  # skip the header line
        stats = [[float(stat) for stat in line.split(";")[1:]] for line in lines]
        saves_percentile = round(sum([1 for s in stats if round(s[0], 2) <= round(avg_saves, 2)]) / len(stats) * 100, 2)
        shots_conceded_percentile = round(sum([1 for s in stats if round(s[1], 2) <= round(avg_shots_conceded, 2)]) / len(stats) * 100, 2)
        shots_conceded_percentile = 100 - shots_conceded_percentile
        print(f"Average Saves: {round(avg_saves, 2)} is in the {saves_percentile:.2f} percentile")
        print(f"Average Shots Conceded: {round(avg_shots_conceded, 2)} is in the {shots_conceded_percentile:.2f} percentile")
        return saves_percentile, shots_conceded_percentile

def get_player_card_stats(player, download_new_replays, session_stats, count):
    if download_new_replays:
        # Remove the "replays" directory
        if os.path.exists("replays"):
            os.system("rmdir /s /q replays")
        download_player_replays(player, count)

    print(f"Stats for {player}:")

    offensive_war, games_sampled = calculate_average_offensive_war(player)
    get_offensive_war_percentile(offensive_war)

    defensive_war, games_sampled = calculate_average_defensive_war(player)
    get_defensive_war_percentile(defensive_war)

    player_war = calculate_war(offensive_war, defensive_war)
    get_war_percentile(player_war)

    avg_goals, avg_assists, avg_shot_acc, avg_avg_boost, avg_demos_inflicted, avg_shots, num_games = calculate_avg_offensive_stats(player)
    get_offensive_stats_percentile(avg_goals, avg_assists, avg_shot_acc, avg_avg_boost, avg_demos_inflicted, avg_shots)

    avg_saves, avg_shots_conceded, num_games = calculate_avg_defensive_stats(player)
    get_defensive_stats_percentile(avg_saves, avg_shots_conceded)

    if session_stats:
        PPG = avg_goals + avg_assists
        print(f"{avg_goals} goals per game")
        print(f"{avg_assists} assists per game")
        print(f"{PPG} points per game")
        print(f"{avg_saves} saves per game")

    # Get the total number of replays
    replay_count = sum(len(files) for _, _, files in os.walk("replays"))
    print(f"Total number of replays: {replay_count}")

    return

def get_pro_card_stats(player):
    print(f"Stats for {player}:")
    update_cell_with_text("D2:O2", player)
    with open("player_offensive_war.txt", "r") as f:
        lines = f.readlines()[0:]  # skip the header line
        for line in lines:
            if line.startswith(player):
                _, stats = line.strip().split(":")
                offensive_war, num_games = stats.split(",")
                offensive_war_percentile = get_offensive_war_percentile(float(offensive_war))
                update_cell_with_percentage("B10:C11", offensive_war_percentile)
                break
        else:
            print("Player not found in the offensive WAR stats file.")
    
    with open("player_defensive_war.txt", "r") as f:
        lines = f.readlines()[0:]  # skip the header line
        for line in lines:
            if line.startswith(player):
                _, stats = line.strip().split(":")
                defensive_war, num_games = stats.split(",")
                defensive_war_percentile = get_defensive_war_percentile(float(defensive_war))
                update_cell_with_percentage("E10:F11", defensive_war_percentile)
                break
        else:
            print("Player not found in the defensive WAR stats file.")
    
    with open("player_war.txt", "r") as f:
        lines = f.readlines()[0:]  # skip the header line
        for line in lines:
            if line.startswith(player):
                _, stats = line.strip().split(":")
                player_war, num_games = stats.split(",")
                player_war_percentile = get_war_percentile(float(player_war))
                update_cell_with_percentage("G5:I7", player_war_percentile)
                break
        else:
            print("Player not found in the overall WAR stats file.")
    
    with open("player_offensive_stats.txt", "r") as f:
        lines = f.readlines()[0:]  # skip the header line
        for line in lines:
            if line.startswith(player):
                _, avg_goals, avg_assists, avg_shot_acc, avg_avg_boost, avg_demos_inflicted, avg_shots, num_games = line.strip().split(";")
                goals_percentile, assists_percentile, shot_acc_percentile, avg_boost_percentile, demos_inflicted_percentile, shots_percentile = get_offensive_stats_percentile(float(avg_goals), float(avg_assists), float(avg_shot_acc), float(avg_avg_boost), float(avg_demos_inflicted), float(avg_shots))
                update_cell_with_percentage("B14:C15", goals_percentile)
                update_cell_with_percentage("E14:F15", assists_percentile)
                update_cell_with_percentage("M10:N11", shot_acc_percentile)
                update_cell_with_percentage("H10:H11", avg_boost_percentile)
                update_cell_with_percentage("H14:H15", demos_inflicted_percentile)
                update_cell_with_percentage("J10:K11", shots_percentile)
                update_cell_with_text("L7:O7", num_games)
                break
        else:
            print("Player not found in the offensive stats file.")

    with open("player_defensive_stats.txt", "r") as f:
        lines = f.readlines()[0:] #  skip the header line
        for line in lines:
            if line.startswith(player):
                _, avg_saves, avg_shots_conceded, num_games = line.strip().split(";")
                saves_percentile, shots_conceded_percentile = get_defensive_stats_percentile(float(avg_saves), float(avg_shots_conceded))
                update_cell_with_percentage("J14:K15", saves_percentile)
                update_cell_with_percentage("M14:N15", shots_conceded_percentile)
                break
        else:
            print("Player not found in the defensive stats file.")
    
    name_corrections = {}

    # Load name corrections from file
    with open('name_corrections.txt', 'r') as file:
        for line in file:
            in_game_name, online_name = line.strip().split(';')
            name_corrections[in_game_name] = online_name

    if player in name_corrections:
            corrected_player = name_corrections[player]
            team_name, player_age, team_logo, _ = get_pro_player_info(corrected_player)
    else:
        team_name, player_age, team_logo, _ = get_pro_player_info(player)

    update_cell_with_text("L5:O5", team_name)
    update_cell_with_text("L4:O4", player_age)
    update_cell_with_image("B2:C2", team_logo)

get_pro_card_stats("M7sn")

# Gen.G Mobil1 Racing; ApparentlyJack, Chronic, noly

















