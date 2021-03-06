#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
import os
import ray
from ray import tune
from ..optmizers import get_best_model
from ..utils.functions import _parse_params
import pickle
import onnxmltools
import numpy as np


import matplotlib.pyplot as plt
import time
import keras.backend as K
import warnings
warnings.filterwarnings("ignore")


class IrtKerasRegressor():
    """
        Adapter to connect Irt Rasch One Parameter, Two parameter model and Birnbaum's Three Parameter model with keras models.

    This class is used as an adapter for a IRT signature model that initilalises with similar parameters along the line of R's
    Rasch and tpm(3-PL) models from ltm package that are as proxy models using keras.

    Parameters
    ----------
    proxy_model : proxy model instance
        The proxy model passed from dope.

    primal_model : primal model instance
        The primal model passed from dope.

    params : dict, optional
        Additional model params passed by the user.


    Methods
    -------
        fit(X_users, X_questions, y)
        Method to train a transpiled model.

    plot()
        Method to plot model's train-validation loss.

    coefficients()
        Method to output model coefficients -- Difficulty level,
        Discrimination parameter, Guessing params

        save(filename)
        Method to save a trained model. This method saves
        the models in three formals -- pickle, h5 and onnx.
        Expects 'filename' as a string.

        score(X, y)
        Method to score a trained model.

        predict(X)
        This method returns the predicted values for a
        trained model.

        explain()
        Method to provide model interpretations(Yet to be implemented)

    """

    def __init__(self, proxy_model, primal_model, **kwargs):
        kwargs.setdefault('params', None)
        self.primal_model = primal_model
        self.proxy_model = proxy_model
        self.proxy_model.primal = self.primal_model
        self.params = kwargs['params']

    def fit(self, x_user, x_questions, y_vals, **kwargs):
        kwargs.setdefault('latent_traits', None)
        kwargs.setdefault('batch_size', 16)
        kwargs.setdefault('epochs', 64)
        kwargs.setdefault('validation_split', 0.2)
        kwargs.setdefault('params', self.params)

        self.proxy_model.l_traits = kwargs['latent_traits']

        self.proxy_model.x_train_user = x_user
        self.proxy_model.x_train_questions = x_questions
        self.proxy_model.y_ = y_vals

        self.l_traits = kwargs['latent_traits']
        # affirming if params are given in either of(init or fit) methods
        self.params = self.params or kwargs['params']
        if self.params != None:  # Validate implementation with different types of tune input
            if not isinstance(self.params, dict):
                raise TypeError("Params should be of type 'dict'")
            self.params = _parse_params(self.params, return_as='flat')
            self.proxy_model.update_params(self.params)
            # triggers for fourPL model
            if self.proxy_model.name is 'tpm' and 'slip_params' in self.params and 'train' in self.params['slip_params'].keys():
                if self.params['slip_params']['train']:
                    self.proxy_model.name = 'fourPL'

        ray_verbose = False
        _ray_log_level = logging.INFO if ray_verbose else logging.ERROR
        ray.init(log_to_driver=False, logging_level=_ray_log_level, ignore_reinit_error=True, redis_max_memory=20*1000*1000*1000, object_store_memory=1000000000,
                 num_cpus=4)

        def train_model(config, reporter):
            self.proxy_model.set_params(params=config, set_by='optimizer')
            print('\nIntitializing fit for {} model. . .\nBatch_size: {}; epochs: {};'.format(
                self.proxy_model.name, kwargs['batch_size'], kwargs['epochs']))
            model = self.proxy_model.create_model()

            self.history = model.fit(x=[x_user, x_questions], y=y_vals, batch_size=kwargs['batch_size'],
                                     epochs=kwargs['epochs'], verbose=0, validation_split=kwargs['validation_split'])

            _, mae, accuracy = model.evaluate(
                x=[x_user, x_questions], y=y_vals)  # [1]
            last_checkpoint = "weights_tune_{}.h5".format(
                list(zip(np.random.choice(10, len(config), replace=False), config)))
            model.save_weights(last_checkpoint)
            reporter(mean_error=mae, mean_accuracy=accuracy,
                     checkpoint=last_checkpoint)
        t1 = time.time()
        configuration = tune.Experiment("experiment_name",
                                        run=train_model,
                                        resources_per_trial={"cpu": 4},
                                        stop={"mean_error": 0.15,
                                              "mean_accuracy": 95},
                                        config=self.proxy_model.get_params())

        trials = tune.run_experiments(configuration, verbose=0)
        self.trials = trials
        metric = "mean_error"  # "mean_accuracy"
        # Restore a model from the best trial.

        def get_sorted_trials(trial_list, metric):
            return sorted(trial_list, key=lambda trial: trial.last_result.get(metric, 0), reverse=True)

        sorted_trials = get_sorted_trials(trials, metric)

        for best_trial in sorted_trials:
            try:
                print("Creating model...")
                self.proxy_model.set_params(
                    params=best_trial.config, set_by='optimizer')
                best_model = self.proxy_model.create_model()
                weights = os.path.join(
                    best_trial.logdir, best_trial.last_result["checkpoint"])
                print("Loading from", weights)
                # TODO Validate this loaded model.
                best_model.load_weights(weights)
                break
            except Exception as e:
                print(e)
                print("Loading failed. Trying next model")
        exe_time = time.time()-t1
        self.model = best_model

        #self.model = model
        #print('\nIntitializing fit for {} model. . .\nBatch_size: {}; epochs: {};'.format(self.proxy_model.name, kwargs['batch_size'], kwargs['epochs']))
        #model = self.proxy_model.create_model()
        #t1= time.time()
        # self.history= model.fit(x=[x_user, x_questions], y=y_vals, batch_size=kwargs['batch_size'], epochs=kwargs['epochs'], verbose=0, validation_split=kwargs['validation_split'])#, callbacks= kwargs['callbacks'])#added callbacks
        #exe_time = time.time()-t1
#
        #self.model = model

        # Following lets user access each coeffs as and when required
        self.difficulty = self.coefficients()['difficulty_level']
        self.discrimination = self.coefficients()['disc_param']
        self.guessing = self.coefficients()['guessing_param']
        self.slip = self.coefficients()['slip_param']

        num_trainables = np.sum([K.count_params(layer)
                                 for layer in self.model.trainable_weights])
        sample_size = y_vals.shape[0]
        log_lik, _, _ = self.model.evaluate(x=[x_user, x_questions], y=y_vals)

        self.AIC = 2*num_trainables - 2*np.log(log_lik)
        self.AICc = self.AIC + (2*np.square(num_trainables) +
                                2*num_trainables)/(sample_size - num_trainables - 1)

        print('\nTraining on : {} samples for : {} epochs has completed in : {} seconds.'.format(
            self.proxy_model.x_train_user.shape[0], kwargs['epochs'], np.round(exe_time, decimals=3)))
        print('\nAIC value: {} and AICc value: {}'.format(
            np.round(self.AIC, 3), np.round(self.AICc, 3)))

        print('\nUse `object.plot()` to view train/validation loss curves;\nUse `object.history` to obtain train/validation loss across all the epochs.\nUse `object.coefficients()` to obtain model parameters--Question difficulty, discrimination, guessing & slip')
        print('Use `object.AIC` & `object.AIC` to obtain Akaike Information Criterion(AIC & AICc) values.')
        return self

    def plot(self):
        plt.plot(self.history.history['loss'])
        plt.plot(self.history.history['val_loss'])
        plt.title('Model loss for "{} model" '.format(self.proxy_model.name))
        plt.xlabel('epoch')
        plt.ylabel('loss')

        plt.legend(['train', 'validation'], loc='upper right')
        return plt.show()

    def coefficients(self):
        rel_layers_idx = list()
        for idx, layer in enumerate(self.model.layers):
            if layer.name in ['latent_trait/ability', 'difficulty_level', 'disc_param', 'guessing_param', 'slip_param']:
                rel_layers_idx.append(idx)

        coef = {self.model.layers[idx].name: self.model.layers[idx].get_weights()[
            0] for idx in rel_layers_idx}
        t_4PL = {'tpm': ['guessing_param'], 'fourPL': [
            'guessing_param', 'slip_param']}
        if self.proxy_model.name in t_4PL.keys():  # reporting guess & slip
            for layer in t_4PL[self.proxy_model.name]:
                coef.update(
                    {layer: np.exp(coef[layer])/(1 + np.exp(coef[layer]))})

        coef.update({'disc_param': np.exp(coef['disc_param'])})
        # if not self.proxy_model.name=='tpm':#for 1PL & 2PL
        #    coef.update({'disc_param':np.exp(coef['disc_param'])})
        # else:
        #    coef.update({'guessing_param':np.exp(coef['guessing_param'])/(1+ np.exp(coef['guessing_param']))})
        #    coef.update({'disc_param':np.exp(coef['disc_param'])})
        return coef

    def predict(self, x_user, x_questions):
        if len(x_user.shape) != len(self.proxy_model.x_train_user.shape) or len(x_questions.shape) != len(self.proxy_model.x_train_user.shape):
            raise ValueError("While checking User/Question input shape, Expected users to have shape(None,{}) and questions to have shape(None,{})".format(
                self.proxy_model.x_train_user.shape[1], self.proxy_model.x_train_questions.shape[1]))
        if x_user.shape[1] != self.proxy_model.x_train_user.shape[1] or x_questions.shape[1] != self.proxy_model.x_train_questions.shape[1]:
            raise ValueError("User/Question seem to be an anomaly to current training dataset; Expected Users to have shape(None,{}) and Questions to have shape(None,{})".format(
                self.proxy_model.x_train_user.shape[1], self.proxy_model.x_train_questions.shape[1]))
        pred = self.model.predict([x_user, x_questions])
        return pred


class SklearnTfTransformer():
    """
        Adapter to connect sklearn decomposition methods to respective TF implementations.

    This class can be used as an adapter for primal decomposition methods that can
    utilise TF backend for proxy model.

    Parameters
    ----------
    proxy_model : proxy model instance
        The proxy model passed from dope.

    primal_model : primal model instance
        The primal model passed from dope.

    params : dict, optional
        Additional model params passed by the user.


    Methods
    -------
        fit(X, y)
        Method to train a transpiled model

        transform(X)
        Method to transform the input matrix to truncated dimensions;
        Only once the decomposed values are computed.

        fit_transform(X)
        Method to right away transform the input matrix to truncated dimensions.

        inverse_transform(X)
        This method returns Original values from the resulting decomposed matrices.

    """

    def __init__(self, proxy_model, primal_model, **kwargs):
        self.primal_model = primal_model
        self.proxy_model = proxy_model
        self.proxy_model.primal = self.primal_model
        self.params = None

    def fit(self, X, y=None, **kwargs):
        self.proxy_model.X = X
        self.proxy_model.y = y

        if self.params != None:  # Validate implementation with different types of tune input
            if not isinstance(self.params, dict):
                raise TypeError("Params should be of type 'dict'")
            self.params = _parse_params(self.params, return_as='flat')
            self.proxy_model.update_params(self.params)

        self.fit_transform(X)
        # self.proxy_model.fit(X)
        #self.params = self.proxy_model.get_params()
        # to avoid calling model.fit(X).proxy_model for sigma & Vh
        #self.components_= self.params['components_']
        #self.singular_values_= self.params['singular_values_']
        return self

    def transform(self, X):
        return self.proxy_model.transform(X)

    def fit_transform(self, X, y=None):
        x_transformed = self.proxy_model.fit_transform(X)
        self.params = self.proxy_model.get_params()
        # to avoid calling model.fit(X).proxy_model for sigma & Vh
        self.components_ = self.params['components_']
        self.singular_values_ = self.params['singular_values_']
        return x_transformed

    def inverse_transform(self, X):
        return self.proxy_model.inverse_transform(X)


class SklearnKerasClassifier():
    """
        Adapter to connect sklearn classifier algorithms with keras models.

    This class can be used as an adapter for any primal classifier that relies
    on keras as the backend for proxy model.

    Parameters
    ----------
    proxy_model : proxy model instance
        The proxy model passed from dope.

    primal_model : primal model instance
        The primal model passed from dope.

    params : dict, optional
        Additional model params passed by the user.


    Methods
    -------
        fit(X, y)
        Method to train a transpiled model

        save(filename)
        Method to save a trained model. This method saves
        the models in three formals -- pickle, h5 and onnx.
        Expects 'filename' as a string.

        score(X, y)
        Method to score a trained model.

        predict(X)
        This method returns the predicted values for a
        trained model.

        explain()
        Method to provide model interpretations(Yet to be implemented)

    """

    def __init__(self, proxy_model, primal_model, **kwargs):
        self.primal_model = primal_model
        self.params = None  # Temporary!
        self.proxy_model = proxy_model

    def fit(self, X, y, **kwargs):
        kwargs.setdefault('cuts_per_feature', None)  # Better way to handle?

        # For all models?
        self.proxy_model.cuts_per_feature = kwargs['cuts_per_feature']
        kwargs.setdefault('verbose', 0)
        kwargs.setdefault('params', self.params)
        kwargs.setdefault('space', False)
        kwargs.setdefault('epochs', 250)
        kwargs.setdefault('batch_size', 30)
        self.params = kwargs['params']
        X = np.array(X)
        y = np.array(y)

        primal_model = self.primal_model
        primal_model.fit(X, y)
        y_pred = primal_model.predict(X)

        X, y, y_pred = self.proxy_model.transform_data(X, y, y_pred)

        # This should happen only after transformation.
        self.proxy_model.X = X  # abstract -> model_skeleton
        self.proxy_model.y = y
        self.proxy_model.primal = self.primal_model

        if self.params != None:  # Validate implementation with different types of tune input
            if not isinstance(self.params, dict):
                raise TypeError("Params should be of type 'dict'")
            self.params = _parse_params(self.params, return_as='flat')
            self.proxy_model.update_params(self.params)

        primal_data = {  # Consider renaming -- primal_model_data or primal_results
            'y_pred': y_pred,
            'model_name': primal_model.__class__.__name__
        }

        ## Search for best model using Tune ##
        self.final_model = get_best_model(X, y, proxy_model=self.proxy_model,
                                          primal_data=primal_data, epochs=kwargs[
                                              'epochs'], batch_size=kwargs['batch_size'],
                                          verbose=kwargs['verbose'])
        return self.final_model  # Return self? IMPORTANT

    def save(self, filename=None):
        if filename == None:
            raise ValueError(
                'Name Error: to save the model you need to specify the filename')

        pickle.dump(self.final_model, open(filename + '.pkl', 'wb'))

        self.final_model.save(filename + '.h5')

        onnx_model = onnxmltools.convert_keras(self.final_model)
        onnxmltools.utils.save_model(onnx_model, filename + '.onnx')

    def score(self, X, y, **kwargs):
        if self.proxy_model.enc is not None:
            # Should we accept pandas?
            y = np.array(y)
            X = np.array(X)
            if len(y.shape) == 1 or y.shape[1] == 1:
                y = self.proxy_model.enc.transform(y.reshape(-1, 1))
                y = y.toarray()  # Cross check with logistic regression flow
            else:
                y = self.proxy_model.enc.transform(y)
                y = y.toarray()
        score = self.final_model.evaluate(X, y, **kwargs)
        return score

    def predict(self, X):
        X = np.array(X)
        if hasattr(self.final_model, 'predict_classes'):
            pred = self.final_model.predict_classes(X)
        else:
            pred = self.final_model.predict(X)
            pred = np.argmax(pred, axis=1)
        return pred

    def predict_proba(self, X):
        pass

    def explain(self, **kwargs):
        # @param: SHAP or interpret
        print('Coming soon...')
        return self.final_model.summary()


class SklearnKerasRegressor():
    """
        Adapter to connect sklearn regressor algorithms with keras models.

    This class can be used as an adapter for any primal regressor that relies
    on keras as the backend for proxy model.

    Parameters
    ----------
    proxy_model : proxy model instance
        The proxy model passed from dope.

    primal_model : primal model instance
        The primal model passed from dope.

    params : dict, optional
        Additional model params passed by the user.


    Methods
    -------
        fit(X, y)
        Method to train a transpiled model

        save(filename)
        Method to save a trained model. This method saves
        the models in three formals -- pickle, h5 and onnx.
        Expects 'filename' as a string.

        score(X, y)
        Method to score a trained model.

        predict(X)
        This method returns the predicted values for a
        trained model.

        explain()
        Method to provide model interpretations(Yet to be implemented)

    """

    def __init__(self, proxy_model, primal_model, **kwargs):
        self.primal_model = primal_model
        self.proxy_model = proxy_model
        self.params = None

    def fit(self, X, y=None, **kwargs):
        self.proxy_model.X = X
        self.proxy_model.y = y
        self.proxy_model.primal = self.primal_model
        kwargs.setdefault('verbose', 0)
        kwargs.setdefault('epochs', 250)
        kwargs.setdefault('batch_size', 30)
        kwargs.setdefault('params', self.params)
        self.params = kwargs['params']

        if self.params != None:  # Validate implementation with different types of tune input
            if not isinstance(self.params, dict):
                raise TypeError("Params should be of type 'dict'")
            self.params = _parse_params(self.params, return_as='flat')
            self.proxy_model.update_params(self.params)
        primal_model = self.primal_model
        primal_model.fit(X, y)
        y_pred = primal_model.predict(X)
        primal_data = {
            'y_pred': y_pred,
            'model_name': primal_model.__class__.__name__
        }

        self.final_model = get_best_model(X, y, proxy_model=self.proxy_model, primal_data=primal_data,
                                          epochs=kwargs['epochs'], batch_size=kwargs['batch_size'],
                                          verbose=kwargs['verbose'])
        return self.final_model  # Not necessary.

    def score(self, X, y, **kwargs):
        score = self.final_model.evaluate(X, y, **kwargs)
        return score

    def predict(self, X):
        '''
        Pending:
        1) Write a 'filter_sk_params' function(check keras_regressor wrapper) if necessary.
        2) Data checks and data conversions
        '''
        pred = self.final_model.predict(X)
        return pred

    def save(self, filename=None):
        if filename == None:
            raise ValueError(
                'Name Error: to save the model you need to specify the filename')
        pickle.dump(self.final_model, open(filename + '.pkl', 'wb'))

        self.final_model.save(filename + '.h5')

        onnx_model = onnxmltools.convert_keras(self.final_model)
        onnxmltools.utils.save_model(onnx_model, filename + '.onnx')

    def explain(self, **kwargs):
        # @param: SHAP or interpret
        print('Coming soon...')
        return self.final_model.summary()


class SklearnPytorchClassifier():
    def __init__(self, proxy_model, primal_model, **kwargs):
        self.primal_model = primal_model
        self.params = None  # Temporary!
        self.proxy_model = proxy_model

    def fit(self, X, y, **kwargs):
        self.proxy_model.X = X
        self.proxy_model.y = y
        self.proxy_model.primal = self.primal_model

        for epoch in range(50):
            # Forward Propagation
            # Access model, criterion and optimizer from proxy_model
            # Alter how tune computes `fit`. Override keras_model.fit option
            y_pred = model(x)    # Compute and print loss
            loss = criterion(y_pred, y)
            # Zero the gradients
            print('epoch: ', epoch, ' loss: ', loss.item())
            optimizer.zero_grad()

            # perform a backward pass (backpropagation)
            loss.backward()

            # Update the parameters
            optimizer.step()


# TODO
# predict_proba implementation
# filter_sklearn_params method
