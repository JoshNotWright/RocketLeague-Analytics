import pandas as pd
import numpy as np
from utils.populate_team_cards import *
from war_calculation.offensive_war import calculate_average_offensive_war, calculate_avg_offensive_stats
from war_calculation.defensive_war import calculate_average_defensive_war, calculate_avg_defensive_stats
from utils.replay_downloader import download_player_replays
from war_calculation.calculate_war import calculate_war
import os
from utils.get_pro_player_info import get_pro_player_info, extract_team_logo, extract_team_region

def get_offensive_war_rank(score):
    with open("team_stats.txt", "r") as f:
        lines = f.readlines()[0:]  # skip the header line
        scores = []
        for line in lines:
            parts = line.strip().split(":")[1].split(";")
            offensive_war = float(parts[0])
            scores.append(offensive_war)
        rounded_score = round(score, 2)
        rank = sorted(scores, reverse=True).index(rounded_score) + 1
        print(f"{rounded_score} ranks {rank}/{len(scores)} in Offensive WAR")
        return rank


def get_defensive_war_rank(score):
    with open("team_stats.txt", "r") as f:
        lines = f.readlines()[0:]  # skip the header line
        scores = []
        for line in lines:
            parts = line.strip().split(":")[1].split(";")
            defensive_war = float(parts[1])
            scores.append(defensive_war)
        rounded_score = round(score, 2)
        rank = sorted(scores, reverse=True).index(rounded_score) + 1
        print(f"{rounded_score} ranks {rank}/{len(scores)} in Defensive WAR")
        return rank


def get_war_rank(score):
    with open("team_stats.txt", "r") as f:
        lines = f.readlines()[0:]  # skip the header line
        scores = []
        for line in lines:
            parts = line.strip().split(":")[1].split(";")
            team_war = float(parts[2])
            scores.append(team_war)
        rounded_score = round(score, 2)
        rank = sorted(scores, reverse=True).index(rounded_score) + 1
        print(f"{rounded_score} ranks {rank}/{len(scores)} in Overall WAR")
        return rank


def get_offensive_stats_rank(avg_goals, avg_assists, avg_shot_acc, avg_avg_boost, avg_demos_inflicted, avg_shots):
    with open("team_stats.txt", "r") as f:
        lines = f.readlines()[0:]  # skip the header line
        stats = [[float(stat) for stat in line.strip().split(":")[1].split(";")[3:9]] for line in lines]
        goals_rank = sorted([s[0] for s in stats], reverse=True).index(avg_goals) + 1
        assists_rank = sorted([s[1] for s in stats], reverse=True).index(avg_assists) + 1
        shot_acc_rank = sorted([s[2] for s in stats], reverse=True).index(avg_shot_acc) + 1
        avg_boost_rank = sorted([s[3] for s in stats], reverse=True).index(avg_avg_boost) + 1
        demos_inflicted_rank = sorted([s[4] for s in stats], reverse=True).index(avg_demos_inflicted) + 1
        shots_rank = sorted([s[5] for s in stats], reverse=True).index(avg_shots) + 1
        print(f"Average Goals: {round(avg_goals, 2)} ranks {goals_rank}/{len(stats)}")
        print(f"Average Assists: {round(avg_assists, 2)} ranks {assists_rank}/{len(stats)}")
        print(f"Average Shot Accuracy: {round(avg_shot_acc, 2)} ranks {shot_acc_rank}/{len(stats)}")
        print(f"Average Boost: {round(avg_avg_boost, 2)} ranks {avg_boost_rank}/{len(stats)}")
        print(f"Average Demos Inflicted: {round(avg_demos_inflicted, 2)} ranks {demos_inflicted_rank}/{len(stats)}")
        print(f"Average Shots: {round(avg_shots, 2)} ranks {shots_rank}/{len(stats)}")
        return goals_rank, assists_rank, shot_acc_rank, avg_boost_rank, demos_inflicted_rank, shots_rank


def get_defensive_stats_rank(avg_saves, avg_shots_conceded):
    with open("team_stats.txt", "r") as f:
        lines = f.readlines()[0:]  # skip the header line
        stats = [[float(stat) for stat in line.strip().split(":")[1].split(";")[9:11]] for line in lines]
        saves_rank = sorted([s[0] for s in stats], reverse=True).index(avg_saves) + 1
        shots_conceded_rank = sorted([s[1] for s in stats], reverse=False).index(avg_shots_conceded) + 1
        print(f"Average Saves: {round(avg_saves, 2)} ranks {saves_rank}/{len(stats)}")
        print(f"Average Shots Conceded: {round(avg_shots_conceded, 2)} ranks {shots_conceded_rank}/{len(stats)}")
        return saves_rank, shots_conceded_rank

def get_team_stats(team):
    print(f"Stats for {team}:")
    with open("team_stats.txt", "r") as f:
        lines = f.readlines()[0:]  # skip the header line
        found = False
        for line in lines:
            if line.startswith(team):
                found = True
                parts = line.strip().split(":")
                _, stats = parts[0], parts[1].split(";")
                
                offensive_war = float(stats[0])
                defensive_war = float(stats[1])
                team_war = float(stats[2])
                avg_goals = float(stats[3])
                avg_assists = float(stats[4])
                avg_shot_acc = float(stats[5])
                avg_boost = float(stats[6])
                demos_inflicted = float(stats[7])
                shots = float(stats[8])
                saves = float(stats[9])
                shots_conceded_avg = float(stats[10])
                
                offensive_war_rank = get_offensive_war_rank(offensive_war)
                defensive_war_rank = get_defensive_war_rank(defensive_war)
                team_war_rank = get_war_rank(team_war)
                goals_rank, assists_rank, shot_acc_rank, avg_boost_rank, demos_inflicted_rank, shots_rank = get_offensive_stats_rank(avg_goals, avg_assists, avg_shot_acc, avg_boost, demos_inflicted, shots)
                saves_rank, shots_conceded_rank = get_defensive_stats_rank(saves, shots_conceded_avg)

                update_cell_with_text("D2:N2", team)
                update_cell_with_image("B4:E7", extract_team_logo(team))
                update_cell_with_text("K5:M7", extract_team_region(team))

                update_cell_with_number("G5:I7", team_war_rank)
                update_cell_with_number("B10:C11", offensive_war_rank)
                update_cell_with_number("E10:F11", defensive_war_rank)
                update_cell_with_number("H10:H11", avg_boost_rank)
                update_cell_with_number("J10:K11", shots_rank)
                update_cell_with_number("M10:N11", shot_acc_rank)
                update_cell_with_number("B14:C15", goals_rank)
                update_cell_with_number("E14:F15", assists_rank)
                update_cell_with_number("H14:H15", demos_inflicted_rank)
                update_cell_with_number("J14:K15", saves_rank)
                update_cell_with_number("M14:N15", shots_conceded_rank)
                break

        if not found:
            print("Team not found in the team stats file.")

get_team_stats("Team BDS")
