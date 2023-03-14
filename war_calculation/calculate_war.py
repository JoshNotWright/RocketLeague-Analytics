import os
import sys

# add the path to the root directory to the system path
root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(root_path)

# now you can import the module
from utils.scale_war import scale_war


def calculate_war(offensive_war: float, defensive_war: float) -> float:
    offensive_weight = 1
    defensive_weight = 1
    offensive_war = scale_war(offensive_war, "offensive")
    defensive_war = scale_war(defensive_war, "defensive")
    return (offensive_war * offensive_weight) + (defensive_war * defensive_weight)




