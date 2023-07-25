import csv
import os

def get_match_id(csv_path):
    csv_path = os.path.join("replays", csv_path)
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f, delimiter=';')
        rows = list(reader)  # convert reader to a list
        player_ids = [row['player id'][:2] for row in rows]
        top_player_id = player_ids[0]
        score_sum = sum([int(row['score']) for row in rows])
        match_id = ''.join(player_ids) + str(score_sum)
        return match_id

