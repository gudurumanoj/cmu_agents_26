## RL 1
Reward is just a scalar value that lets the agent know how good or bad it did

SFT trains the agent or model to maximize the probablity of actions that are present in the demonstrations.

SFT has three issues
- It maximizes the probablity of actions that are present in the demonstrations but we would like to maximize the probability that the agnet completes the task successfully
    - There would be multiple ways to complete a task successfully
    - Some actions are worse than others and for sft, all might look equal from the actions that weren't demonstrated
- Data mismatches
    - We'd also want to learn from sub-optimal data. We would not do sft on low quality data because we dont want the agent to maximize incorrect demonstrations
- Exposure bias
    - The agent was not exposed to its own mistakes and correcting them
    - Limited by the coverage of the demonstrations

The property of training the model by generating is usually called on-policy training.

***In RL, the parameters are updated in order to maximize the expected reward***.

![RL](../assets/lec9/rl.png)

In a lot of cases, we have an end reward but in some cases, there can be intermediate rewards as well

RFT (reinforced SFT)

![RFT](../assets/lec9/rft.png)

The loss here for RFT is just standard sft on good trajectories which are obtained by filtering the self generated data

![RFT 2](../assets/lec9/rft2.png)

RL does something similar like above in a more fine-grained way

Curriculum training adn intermediate rewards

We cant directly use backprop to maximize the expected reward because sampling and environment are non-differentiable to use chain rule and take gradients through them.

![REINFORCE](../assets/lec9/reinforce.png)

So we use `reinforce` to approximate the gradient of the expected reward with respect to the parameters.

![REINFORCE policy gradient](../assets/lec9/reinforce1.png)
![REINFORCE policy gradient](../assets/lec9/reinforce2.png)

The observations' probability of occuring is independent of the parameters and so its differentiation wrt parameters of the lm is 0.

![REINFORCE policy gradient](../assets/lec9/reinforce3.png)

So the full thing boils down to computing an sft loss kind of thing on the trajectory and multiplying it by the reward of the trajectory.

![REINFORCE policy gradient](../assets/lec9/reinforce4.png)

So its essentially `collect -> evaluate -> form loss -> update params` loop

The update that we do for rewards should really be relative and in reinforce, the reward 0 just removes the learning out from it

![Rewards shoudl be relative](../assets/lec9/rewards_should_be_relative.png)

Advantages come into picture here

![Advantages](../assets/lec9/advantage.png)

Reinforce and advantages arent numerically same but they are different ways to estimating the same thing

![Reinforce v Advantage](../assets/lec9/reinforce_v_advantage.png)

Estimates have bias and variances
- Bias is like how much it is away from the mean of the distribution
- Variance is how much it is spread out

![Bias computation](../assets/lec9/bias0.png)

A huge amount of work in RL is about reducing the variance in the estimates of the gradient
Adding baselines actually helps in reducing this variance

![Without baseline](../assets/lec9/wo_baseline.png)

True value of the gradient is like 0.18 here

![With baseline](../assets/lec9/w_baseline.png)

Look at how the values are closer to 0.18 after adding a suitable baseline compared to the intial estimate which had no baseline. It keeps the mean same but variance changes as we vary the baseline. (in this simple case of two armed bandit)

![Baseline Comp](../assets/lec9/baseline_comp.png)
![Intermediate rewards](../assets/lec9/int_rewards.png)

GRPO: mainly to reduce gpu usage by not using a value functino estimator

![GRPO](../assets/lec9/grpo.png)

Also has clipping and KL divergence penalty to control the step size and avoid divergence respectively

![DR GRPO](../assets/lec9/dr_grpo.png)

No signal if all trajectories pass or if all trajectories fail, we need some spread within our examples to have some signal

![All pass or all fail](../assets/lec9/all_pass_or_faill.png)

![PPO v GRPO](../assets/lec9/ppo_v_grpo.png)