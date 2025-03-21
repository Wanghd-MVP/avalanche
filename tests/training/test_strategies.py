################################################################################
# Copyright (c) 2021 ContinualAI.                                              #
# Copyrights licensed under the MIT License.                                   #
# See the accompanying LICENSE file for terms.                                 #
#                                                                              #
# Date: 1-06-2020                                                              #
# Author(s): Andrea Cossu                                                      #
# E-mail: contact@continualai.org                                              #
# Website: avalanche.continualai.org                                           #
################################################################################
import torch
import unittest

import os
import sys

from torch.optim import SGD
from torch.nn import CrossEntropyLoss, Linear

from avalanche.logging import TextLogger
from avalanche.models import SimpleMLP
from avalanche.training.plugins import EvaluationPlugin, StrategyPlugin, \
    LwFPlugin, ReplayPlugin
from avalanche.training.strategies import Naive, Replay, CWRStar, \
    GDumb, LwF, AGEM, GEM, EWC, \
    SynapticIntelligence, JointTraining, CoPE, StreamingLDA, BaseStrategy
from avalanche.training.strategies.cumulative import Cumulative
from avalanche.training.strategies.strategy_wrappers import PNNStrategy
from avalanche.training.strategies.icarl import ICaRL
from avalanche.training.utils import get_last_fc_layer
from avalanche.evaluation.metrics import StreamAccuracy

from tests.unit_tests_utils import get_fast_scenario, get_device


class BaseStrategyTest(unittest.TestCase):
    def test_periodic_eval(self):
        model = SimpleMLP(input_size=6, hidden_size=10)
        scenario = get_fast_scenario()
        optimizer = SGD(model.parameters(), lr=1e-3)
        criterion = CrossEntropyLoss()
        curve_key = 'Top1_Acc_Stream/eval_phase/train_stream/Task000'

        ###################
        # Case #1: No eval
        ###################
        # we use stream acc. because it emits a single value
        # for each eval loop.
        acc = StreamAccuracy()
        strategy = Naive(model, optimizer, criterion, train_epochs=2,
                         eval_every=-1, evaluator=EvaluationPlugin(acc))
        strategy.train(scenario.train_stream[0])
        # eval is not called in this case
        assert len(strategy.evaluator.get_all_metrics()) == 0

        ###################
        # Case #2: Eval at the end only
        ###################
        acc = StreamAccuracy()
        strategy = Naive(model, optimizer, criterion, train_epochs=2,
                         eval_every=0, evaluator=EvaluationPlugin(acc))
        strategy.train(scenario.train_stream[0])
        # eval is called once at the end of the training loop
        curve = strategy.evaluator.get_all_metrics()[curve_key][1]
        assert len(curve) == 1

        ###################
        # Case #3: Eval after every epoch
        ###################
        acc = StreamAccuracy()
        strategy = Naive(model, optimizer, criterion, train_epochs=2,
                         eval_every=1, evaluator=EvaluationPlugin(acc))
        strategy.train(scenario.train_stream[0])
        # eval is called after every epoch + the end of the training loop
        curve = strategy.evaluator.get_all_metrics()[curve_key][1]
        assert len(curve) == 3

    def test_forward_hooks(self):
        model = SimpleMLP(input_size=6, hidden_size=10)
        optimizer = SGD(model.parameters(), lr=1e-3)
        criterion = CrossEntropyLoss()

        strategy = Naive(model, optimizer, criterion,
                         train_epochs=2, eval_every=0)
        was_hook_called = False

        def hook(a, b, c):
            nonlocal was_hook_called
            was_hook_called = True

        model.register_forward_hook(hook)
        mb_x = torch.randn(32, 6, device=strategy.device)
        strategy.mbatch = mb_x, None, None
        strategy.forward()
        assert was_hook_called

    def test_early_stop(self):
        class EarlyStopP(StrategyPlugin):
            def after_training_iteration(self, strategy: 'BaseStrategy',
                                         **kwargs):
                if strategy.mb_it == 10:
                    strategy.stop_training()

        model = SimpleMLP(input_size=6, hidden_size=100)
        criterion = CrossEntropyLoss()
        optimizer = SGD(model.parameters(), lr=1)

        strategy = Cumulative(
            model, optimizer, criterion, train_mb_size=1, device=get_device(),
            eval_mb_size=512, train_epochs=1, evaluator=None,
            plugins=[EarlyStopP()])
        scenario = get_fast_scenario()

        for train_batch_info in scenario.train_stream:
            strategy.train(train_batch_info)
            assert strategy.mb_it == 11


class StrategyTest(unittest.TestCase):
    if "FAST_TEST" in os.environ:
        fast_test = os.environ['FAST_TEST'].lower() in ["true"]
    else:
        fast_test = False
    if "USE_GPU" in os.environ:
        use_gpu = os.environ['USE_GPU'].lower() in ["true"]
    else:
        use_gpu = False

    print("Fast Test:", fast_test)
    print("Test on GPU:", use_gpu)

    if use_gpu:
        device = "cuda"
    else:
        device = "cpu"

    def init_sit(self):
        model = self.get_model(fast_test=True)
        optimizer = SGD(model.parameters(), lr=1e-3)
        criterion = CrossEntropyLoss()
        scenario = self.load_scenario(use_task_labels=False)
        return model, optimizer, criterion, scenario

    def test_naive(self):
        # SIT scenario
        model, optimizer, criterion, my_nc_benchmark = self.init_sit()
        strategy = Naive(model, optimizer, criterion, train_mb_size=64,
                         device=self.device, eval_mb_size=50, train_epochs=2)
        self.run_strategy(my_nc_benchmark, strategy)

        # MT scenario
        strategy = Naive(model, optimizer, criterion, train_mb_size=64,
                         device=self.device, eval_mb_size=50, train_epochs=2)
        scenario = self.load_scenario(use_task_labels=True)
        self.run_strategy(scenario, strategy)

    def test_joint(self):
        # SIT scenario
        model, optimizer, criterion, my_nc_benchmark = self.init_sit()
        strategy = JointTraining(model, optimizer, criterion, train_mb_size=64,
                                 device=self.device, eval_mb_size=50,
                                 train_epochs=2)
        self.run_strategy(my_nc_benchmark, strategy)

        # MT scenario
        strategy = Naive(model, optimizer, criterion, train_mb_size=64,
                         device=self.device, eval_mb_size=50, train_epochs=2)
        scenario = self.load_scenario(use_task_labels=True)
        self.run_strategy(scenario, strategy)

    def test_cwrstar(self):
        # SIT scenario
        model, optimizer, criterion, my_nc_benchmark = self.init_sit()
        last_fc_name, _ = get_last_fc_layer(model)
        strategy = CWRStar(model, optimizer, criterion, last_fc_name,
                           train_mb_size=64, device=self.device)
        self.run_strategy(my_nc_benchmark, strategy)

        # MT scenario
        strategy = CWRStar(model, optimizer, criterion, last_fc_name,
                           train_mb_size=64, device=self.device)
        scenario = self.load_scenario(use_task_labels=True)
        self.run_strategy(scenario, strategy)

    def test_replay(self):
        # SIT scenario
        model, optimizer, criterion, my_nc_benchmark = self.init_sit()
        strategy = Replay(model, optimizer, criterion,
                          mem_size=10, train_mb_size=64, device=self.device,
                          eval_mb_size=50, train_epochs=2)
        self.run_strategy(my_nc_benchmark, strategy)

        # MT scenario
        strategy = Replay(model, optimizer, criterion,
                          mem_size=10, train_mb_size=64, device=self.device,
                          eval_mb_size=50, train_epochs=2)
        scenario = self.load_scenario(use_task_labels=True)
        self.run_strategy(scenario, strategy)

    def test_gdumb(self):
        # SIT scenario
        model, optimizer, criterion, my_nc_benchmark = self.init_sit()
        strategy = GDumb(
            model, optimizer, criterion,
            mem_size=200, train_mb_size=64, device=self.device,
            eval_mb_size=50, train_epochs=2
        )
        self.run_strategy(my_nc_benchmark, strategy)

        # MT scenario
        strategy = GDumb(
            model, optimizer, criterion,
            mem_size=200, train_mb_size=64, device=self.device,
            eval_mb_size=50, train_epochs=2
        )
        scenario = self.load_scenario(use_task_labels=True)
        self.run_strategy(scenario, strategy)

    def test_cumulative(self):
        # SIT scenario
        model, optimizer, criterion, my_nc_benchmark = self.init_sit()
        strategy = Cumulative(model, optimizer, criterion, train_mb_size=64,
                              device=self.device, eval_mb_size=50,
                              train_epochs=2)
        self.run_strategy(my_nc_benchmark, strategy)

        # MT scenario
        strategy = Cumulative(model, optimizer, criterion, train_mb_size=64,
                              device=self.device, eval_mb_size=50,
                              train_epochs=2)
        scenario = self.load_scenario(use_task_labels=True)
        self.run_strategy(scenario, strategy)

    def test_slda(self):
        model, _, criterion, my_nc_benchmark = self.init_sit()
        strategy = StreamingLDA(model, criterion, input_size=10,
                                output_layer_name='features',
                                num_classes=10, eval_mb_size=7,
                                train_epochs=1, device=self.device,
                                train_mb_size=7)
        self.run_strategy(my_nc_benchmark, strategy)

    def test_warning_slda_lwf(self):
        model, _, criterion, my_nc_benchmark = self.init_sit()
        with self.assertLogs('avalanche.training.strategies', "WARNING") as cm:
            StreamingLDA(model, criterion, input_size=10,
                         output_layer_name='features', num_classes=10,
                         plugins=[LwFPlugin(), ReplayPlugin()])
        self.assertEqual(1, len(cm.output))
        self.assertIn(
            "LwFPlugin seems to use the callback before_backward"
            " which is disabled by StreamingLDA",
            cm.output[0]
        )

    def test_lwf(self):
        # SIT scenario
        model, optimizer, criterion, my_nc_benchmark = self.init_sit()
        strategy = LwF(model, optimizer, criterion,
                       alpha=[0, 1 / 2, 2 * (2 / 3), 3 * (3 / 4), 4 * (4 / 5)],
                       temperature=2, device=self.device,
                       train_mb_size=10, eval_mb_size=50,
                       train_epochs=2)
        self.run_strategy(my_nc_benchmark, strategy)

        # MT scenario
        strategy = LwF(model, optimizer, criterion,
                       alpha=[0, 1 / 2, 2 * (2 / 3), 3 * (3 / 4), 4 * (4 / 5)],
                       temperature=2, device=self.device,
                       train_mb_size=10, eval_mb_size=50,
                       train_epochs=2)
        scenario = self.load_scenario(use_task_labels=True)
        self.run_strategy(scenario, strategy)

    def test_agem(self):
        # SIT scenario
        model, optimizer, criterion, my_nc_benchmark = self.init_sit()
        strategy = AGEM(model, optimizer, criterion,
                        patterns_per_exp=250, sample_size=256,
                        train_mb_size=10, eval_mb_size=50,
                        train_epochs=2)
        self.run_strategy(my_nc_benchmark, strategy)

        # MT scenario
        strategy = AGEM(model, optimizer, criterion,
                        patterns_per_exp=250, sample_size=256,
                        train_mb_size=10, eval_mb_size=50,
                        train_epochs=2)
        scenario = self.load_scenario(use_task_labels=True)
        self.run_strategy(scenario, strategy)

    def test_gem(self):
        # SIT scenario
        model, optimizer, criterion, my_nc_benchmark = self.init_sit()
        strategy = GEM(model, optimizer, criterion,
                       patterns_per_exp=256,
                       train_mb_size=10, eval_mb_size=50,
                       train_epochs=2)

        self.run_strategy(my_nc_benchmark, strategy)

        # MT scenario
        strategy = GEM(model, optimizer, criterion,
                       patterns_per_exp=256,
                       train_mb_size=10, eval_mb_size=50,
                       train_epochs=2)
        self.run_strategy(my_nc_benchmark, strategy)
        scenario = self.load_scenario(use_task_labels=True)
        self.run_strategy(scenario, strategy)

    def test_ewc(self):
        # SIT scenario
        model, optimizer, criterion, my_nc_benchmark = self.init_sit()
        strategy = EWC(model, optimizer, criterion, ewc_lambda=0.4,
                       mode='separate',
                       train_mb_size=10, eval_mb_size=50,
                       train_epochs=2)

        self.run_strategy(my_nc_benchmark, strategy)

        # MT scenario
        strategy = EWC(model, optimizer, criterion, ewc_lambda=0.4,
                       mode='separate',
                       train_mb_size=10, eval_mb_size=50,
                       train_epochs=2)
        scenario = self.load_scenario(use_task_labels=True)
        self.run_strategy(scenario, strategy)

    def test_ewc_online(self):
        # SIT scenario
        model, optimizer, criterion, my_nc_benchmark = self.init_sit()
        strategy = EWC(model, optimizer, criterion, ewc_lambda=0.4,
                       mode='online', decay_factor=0.1,
                       train_mb_size=10, eval_mb_size=50,
                       train_epochs=2)
        self.run_strategy(my_nc_benchmark, strategy)

        # MT scenario
        strategy = EWC(model, optimizer, criterion, ewc_lambda=0.4,
                       mode='online', decay_factor=0.1,
                       train_mb_size=10, eval_mb_size=50,
                       train_epochs=2)
        scenario = self.load_scenario(use_task_labels=True)
        self.run_strategy(scenario, strategy)

    def test_synaptic_intelligence(self):
        # SIT scenario
        model, optimizer, criterion, my_nc_benchmark = self.init_sit()
        strategy = SynapticIntelligence(
            model, optimizer, criterion, si_lambda=0.0001,
            train_epochs=1, train_mb_size=10, eval_mb_size=10)
        scenario = self.load_scenario(use_task_labels=False)
        self.run_strategy(scenario, strategy)

        # MT scenario
        strategy = SynapticIntelligence(
            model, optimizer, criterion, si_lambda=0.0001,
            train_epochs=1, train_mb_size=10, eval_mb_size=10)
        scenario = self.load_scenario(use_task_labels=True)
        self.run_strategy(scenario, strategy)

    def test_cope(self):
        # Fast scenario (hardcoded)
        n_classes = 10
        emb_size = n_classes  # Embedding size

        # SIT scenario
        model, optimizer, criterion, my_nc_scenario = self.init_sit()
        strategy = CoPE(model, optimizer, criterion,
                        mem_size=10, n_classes=n_classes, p_size=emb_size,
                        train_mb_size=10, device=self.device,
                        eval_mb_size=50, train_epochs=2)
        self.run_strategy(my_nc_scenario, strategy)

        # MT scenario
        strategy = CoPE(model, optimizer, criterion,
                        mem_size=10, n_classes=n_classes, p_size=emb_size,
                        train_mb_size=10, device=self.device,
                        eval_mb_size=50, train_epochs=2)
        scenario = self.load_scenario(use_task_labels=True)
        self.run_strategy(scenario, strategy)

    def test_pnn(self):
        # only multi-task scenarios.
        # eval on future tasks is not allowed.
        strategy = PNNStrategy(
            num_layers=3, in_features=6, hidden_features_per_column=10,
            lr=0.1, train_mb_size=10, device=self.device, eval_mb_size=50,
            train_epochs=2)

        # train and test loop
        scenario = self.load_scenario(use_task_labels=True)
        for train_task in scenario.train_stream:
            strategy.train(train_task)
        strategy.eval(scenario.test_stream)
        
    def test_icarl(self):
        model, optimizer, criterion, scenario = self.init_sit()

        strategy = ICaRL(
            model.features, model.classifier, optimizer, 20,
            buffer_transform=None, criterion=criterion,
            fixed_memory=True, train_mb_size=10,
            train_epochs=2, eval_mb_size=50,
            device=self.device,)

        self.run_strategy(scenario, strategy)

    def load_scenario(self, use_task_labels=False):
        """
        Returns a NC Scenario from a fake dataset of 10 classes, 5 experiences,
        2 classes per experience.

        :param fast_test: if True loads fake data, MNIST otherwise.
        """
        return get_fast_scenario(use_task_labels=use_task_labels)

    def get_model(self, fast_test=False):
        if fast_test:
            return SimpleMLP(input_size=6, hidden_size=10)
        else:
            return SimpleMLP()

    def run_strategy(self, scenario, cl_strategy):
        print('Starting experiment...')
        cl_strategy.evaluator.loggers = [TextLogger(sys.stdout)]
        results = []
        for train_batch_info in scenario.train_stream:
            print("Start of experience ", train_batch_info.current_experience)

            cl_strategy.train(train_batch_info)
            print('Training completed')

            print('Computing accuracy on the current test set')
            results.append(cl_strategy.eval(scenario.test_stream[:]))


if __name__ == '__main__':
    unittest.main()
