import random
import cProfile
number_of_trials = 10
running_total = 0

number = 1
goal_times_in_a_row = 2

def roll(number, times_in_a_row):
    if random.randint(1,6) == number:
        times_in_a_row += 1
        return times_in_a_row
    else:
        times_in_a_row = 0
        return times_in_a_row
        
    

def find_once(goal_times_in_a_row, number):
    i = 0
    times_in_a_row = 0
    while times_in_a_row < goal_times_in_a_row:
        
        times_in_a_row = roll(number, times_in_a_row)

        i += 1

    return i


# x = find_once(goal_times_in_a_row, number)
# print(f"it took {x} rolls to get {goal_times_in_a_row} {number}'s in a row")
def run_trial(number_of_trials, running_total):
    for trial in range(number_of_trials):

        trial_result = find_once(goal_times_in_a_row, number)

        running_total += trial_result
    return running_total
 
print("final average: " , str(run_trial(number_of_trials, running_total)/number_of_trials))
cProfile.run("run_trial(number_of_trials, running_total)")