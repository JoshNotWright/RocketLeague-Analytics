import pandas as pd
import numpy as np
from scipy.stats import percentileofscore
from war_calculation.offensive_war import calculate_average_offensive_war, calculate_avg_offensive_stats
from war_calculation.defensive_war import calculate_average_defensive_war, calculate_avg_defensive_stats
from utils.replay_downloader import download_player_replays
from war_calculation.calculate_war import calculate_war

def get_offensive_war_percentile(score):
    with open("player_offensive_war.txt", "r") as f:
        lines = f.readlines()[1:] # skip the header line
        scores = []
        for line in lines:
            if line.count(":") > 1:
                _, stats = line.strip().rsplit(":", 1)
            else:
                _, stats = line.strip().split(":")
            offensive_war, num_games = stats.split(",")
            scores.append(float(offensive_war))
        percentile = sum([1 for s in scores if s < score])/len(scores)*100
        print(f"{score} is in the {percentile:.2f} percentile of Diamond 3 in Offensive WAR")
        return percentile
    
def get_defensive_war_percentile(score):
    with open("player_defensive_war.txt", "r") as f:
        lines = f.readlines()[1:] # skip the header line
        scores = []
        for line in lines:
            if line.count(":") > 1:
                _, stats = line.strip().rsplit(":", 1)
            else:
                _, stats = line.strip().split(":")
            defensive_war, num_games = stats.split(",")
            scores.append(float(defensive_war))
        percentile = sum([1 for s in scores if s < score])/len(scores)*100
        print(f"{score} is in the {percentile:.2f} percentile of Diamond 3 in Defensive WAR")
        return percentile
    
def get_war_percentile(score):
    with open("player_war.txt", "r") as f:
        lines = f.readlines()[1:] # skip the header line
        scores = []
        for line in lines:
            if line.count(":") > 1:
                _, stats = line.strip().rsplit(":", 1)
            else:
                _, stats = line.strip().split(":")
            player_war, num_games = stats.split(",")
            scores.append(float(player_war))
        percentile = sum([1 for s in scores if s < score])/len(scores)*100
        print(f"{score} is in the {percentile:.2f} percentile of Diamond 3 in Overall WAR")
        return percentile

def get_offensive_stats_percentile(avg_goals, avg_assists, avg_shot_acc, avg_avg_boost, avg_demos_inflicted, avg_shots):
    with open("player_offensive_stats.txt", "r") as f:
        lines = f.readlines()[1:] # skip the header line
        stats = [[float(stat) for stat in line.split(";")[1:]] for line in lines]
        goals_percentile = sum([1 for s in stats if s[0] < avg_goals])/len(stats)*100
        assists_percentile = sum([1 for s in stats if s[1] < avg_assists])/len(stats)*100
        shot_acc_percentile = sum([1 for s in stats if s[2] < avg_shot_acc])/len(stats)*100
        avg_boost_percentile = sum([1 for s in stats if s[3] < avg_avg_boost])/len(stats)*100
        demos_inflicted_percentile = sum([1 for s in stats if s[4] < avg_demos_inflicted])/len(stats)*100
        shots_percentile = sum([1 for s in stats if s[5] < avg_shots])/len(stats)*100
        print(f"Average Goals: {avg_goals} is in the {goals_percentile:.2f} percentile")
        print(f"Average Assists: {avg_assists} is in the {assists_percentile:.2f} percentile")
        print(f"Average Shot Accuracy: {avg_shot_acc} is in the {shot_acc_percentile:.2f} percentile")
        print(f"Average Boost: {avg_avg_boost} is in the {avg_boost_percentile:.2f} percentile")
        print(f"Average Demos Inflicted: {avg_demos_inflicted} is in the {demos_inflicted_percentile:.2f} percentile")
        print(f"Average Shots: {avg_shots} is in the {shots_percentile:.2f} percentile")
        return goals_percentile, assists_percentile, shot_acc_percentile, avg_boost_percentile, demos_inflicted_percentile, shots_percentile
    
def get_defensive_stats_percentile(avg_saves, avg_shots_conceded):
    with open("player_defensive_stats.txt", "r") as f:
        lines = f.readlines()[1:] # skip the header line
        stats = [[float(stat) for stat in line.split(";")[1:]] for line in lines]
        saves_percentile = sum([1 for s in stats if s[0] < avg_saves])/len(stats)*100
        shots_conceded_percentile = sum([1 for s in stats if s[1] < avg_shots_conceded])/len(stats)*100
        print(f"Average Saves: {avg_saves} is in the {saves_percentile:.2f} percentile")
        print(f"Average Shots Conceded: {avg_shots_conceded} is in the {shots_conceded_percentile:.2f} percentile")
        return saves_percentile, shots_conceded_percentile

def get_player_card_stats(player, download_new_replays, count):
    if download_new_replays:
        download_player_replays(player, count)

    print(f"Stats for {player}:")
    
    defensive_war, games_sampled = calculate_average_defensive_war(player)
    get_defensive_war_percentile(defensive_war)

    offensive_war, games_sampled = calculate_average_offensive_war(player)
    get_offensive_war_percentile(offensive_war)

    avg_goals, avg_assists, avg_shot_acc, avg_avg_boost, avg_demos_inflicted, avg_shots, num_games = calculate_avg_offensive_stats(player)
    get_offensive_stats_percentile(avg_goals, avg_assists, avg_shot_acc, avg_avg_boost, avg_demos_inflicted, avg_shots)

    avg_saves, avg_shots_conceded, num_games = calculate_avg_defensive_stats(player)
    get_defensive_stats_percentile(avg_saves, avg_shots_conceded)

    player_war = calculate_war(offensive_war, defensive_war)
    get_war_percentile(player_war)

    return


get_player_card_stats("PastorRipper", True, 30)

