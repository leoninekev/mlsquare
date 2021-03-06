import numpy as np
import keras
from keras import backend as K
from ..base import registry, BaseModel
from ..adapters.sklearn import IrtKerasRegressor
from keras.layers import Dense, Input
from keras.regularizers import l1_l2
from keras.models import Model
from ..utils.functions import _parse_params
from keras.layers import Lambda
from keras import regularizers
from keras import initializers
from keras.layers import Activation
from keras import metrics
from dict_deep import *
#import copy

class GeneralisedIrtModel(BaseModel):
    """
	A base class for all generalized IRT models -- Rasch(1PL), 2PL, 3PL.

    This class is used as a base class for all IRT models.
    While implementing ensure all required parameters are over written
    with the respective models values. Please refer to LogisticRegression or
    LinearRegression for implementation details.

    Methods
    -------
	create_model(model_params)
        This method contains the base template for IRT models and outputs a keras model.

	set_params(params)
        Method to set model parameters. This method handles the
        flattening of params as well.

	get_params()
        Method to read params.

	update_params(params)
        Method to update params.
    
    tap_update(params):
        Method to update specific model parameters without affect the unspecified defaults

    get_initializers(params):
        Method to update/add kernel initiliazer object passed via model parameters.


    """

    def create_model(self, **kwargs):
        model_params = _parse_params(self._model_params, return_as='nested')
        model_params.update(
            {'input_dims_users': self.x_train_user.shape[1], 'input_dims_items': self.x_train_questions.shape[1]})

        user_input_layer = Input(
            shape=(model_params['input_dims_users'],), name='user_id')
        quest_input_layer = Input(shape=(
            model_params['input_dims_items'],), name='questions/items')

        if not self.l_traits == None:
            pass 
            # provision to add latent traits to first layer
            # latent_trait = Dense(1, use_bias=False,
            #    kernel_initializer= initializers.RandomNormal(mean=0, stddev=1.0, seed=None),
            #    kernel_regularizer=regularizers.l2(0.01), name='latent_trait')(user_input_layer)
        else:
            latent_trait = Dense(model_params['ability_params']['units'], use_bias=model_params['ability_params']['use_bias'],
                                 bias_initializer= model_params['ability_params']['bias'],
                                 kernel_initializer=model_params['ability_params']['kernel'],
                                 kernel_regularizer=l1_l2(
                                        l1=model_params['ability_params']['regularizers']['l1'],
                                        l2=model_params['ability_params']['regularizers']['l2']),
                                 name='latent_trait/ability')(user_input_layer)

        difficulty_level = Dense(model_params['diff_params']['units'], use_bias=model_params['diff_params']['use_bias'],
                                 bias_initializer= model_params['diff_params']['bias'],
                                 kernel_initializer=model_params['diff_params']['kernel'],
                                 kernel_regularizer=l1_l2(
                                        l1=model_params['diff_params']['regularizers']['l1'],
                                        l2=model_params['diff_params']['regularizers']['l2']),
                                 name='difficulty_level')(quest_input_layer)

        discrimination_param = Dense(model_params['disc_params']['units'], use_bias=model_params['disc_params']['use_bias'],
                                     kernel_initializer=model_params['disc_params']['kernel'],
                                     bias_initializer=model_params['disc_params']['bias'],
                                     kernel_regularizer=l1_l2(
                                        l1=model_params['disc_params']['regularizers']['l1'],
                                        l2=model_params['disc_params']['regularizers']['l2']),
                                     trainable=model_params['disc_params']['train'],
                                     activation=model_params['disc_params']['act'],
                                     name='disc_param')(quest_input_layer)

        disc_latent_interaction = keras.layers.Multiply(
            name='lambda_latent_inter.')([discrimination_param, latent_trait])

        disc_diff_interaction = keras.layers.Multiply(
            name='alpha_param.')([discrimination_param, difficulty_level])

        alpha_lambda_add = keras.layers.Subtract(name='alpha_lambda_add')(
            [disc_latent_interaction, disc_diff_interaction])

        sigmoid_layer = Activation(
            'sigmoid', name='Sigmoid_func')(alpha_lambda_add)

        guess_param = Dense(model_params['guess_params']['units'], use_bias=model_params['guess_params']['use_bias'],
                            kernel_initializer=model_params['guess_params']['kernel'],
                            bias_initializer=model_params['guess_params']['bias'],
                            kernel_regularizer=l1_l2(
                                l1=model_params['guess_params']['regularizers']['l1'],
                                l2=model_params['guess_params']['regularizers']['l2']),
                            trainable=model_params['guess_params']['train'],
                            activation=model_params['guess_params']['act'], name='guessing_param')(quest_input_layer)

        slip_param= Dense(model_params['slip_params']['units'], use_bias=model_params['slip_params']['use_bias'],
                            kernel_initializer=model_params['slip_params']['kernel'],
                            bias_initializer=model_params['slip_params']['bias'],
                            kernel_regularizer=l1_l2(
                                l1=model_params['slip_params']['regularizers']['l1'],
                                l2=model_params['slip_params']['regularizers']['l2']),
                            trainable=model_params['slip_params']['train'],
                            activation=model_params['slip_params']['act'], name='slip_param')(quest_input_layer)

        guess_param_interaction = Lambda(lambda x: 1 - x, name='slip_param_inter.')(slip_param)
        guess_param_interaction = keras.layers.Subtract(name= 'slip/guess_interaction')([guess_param_interaction, guess_param])#2

        #guess_param_interaction = Lambda(lambda x: K.constant(value=np.array(
            #[1 - model_params['guess_params']['slip']])) - x, name='guess_param_inter.')(guess_param)

        guess_param_interaction = keras.layers.Multiply(
            name='disc/guess_param_inter.')([sigmoid_layer, guess_param_interaction])

        guess_param_interaction = keras.layers.Add(
            name='guess_param_inter/add')([guess_param, guess_param_interaction])

        prediction_output = Dense(model_params['hyper_params']['units'], trainable=False, use_bias=False,
                                  kernel_initializer=keras.initializers.Ones(), name='prediction_layer')(guess_param_interaction)

        model_ = Model(
            inputs=[user_input_layer, quest_input_layer], outputs=prediction_output)
        model_.compile(loss=model_params['hyper_params']['loss'],
                       optimizer=model_params['hyper_params']['optimizer'], metrics=['mae', 'accuracy'])
        return model_

    def set_params(self, **kwargs):
        kwargs.setdefault('params', None)
        kwargs.setdefault('set_by', None)
        if kwargs['set_by'] == 'model_init':
            # Detect nested or flat at parse_params level
            self._model_params = _parse_params(
                kwargs['params'], return_as='flat')
        elif kwargs['set_by'] == 'opitmizer':
            self._model_params = kwargs['params']
        else:
            self._model_params = kwargs['params']
        # updates dict() with init defautls for the first time
        self.get_initializers(self._model_params)

    def get_params(self):
        return self._model_params

    def tap_update(self, params):
        params_to_tap = self._model_params
        for k, v in params.items():
            if 'kernel' in v and 'kernel_params' not in v:
                deep_del(params_to_tap, [k, 'kernel_params'])
            if 'bias_param' in v.keys():
                params_to_tap[k]['use_bias']= True#self._model_params[k]['use_bias']= True
            for key, val in v.items():
                list_path = [k, key]
                deep_set(params_to_tap, list_path, deep_get(params, list_path),
                         accessor=lambda params_to_tap, k: params_to_tap.setdefault(k, dict()))
        self._model_params = params_to_tap

    def update_params(self, params):
        #print('\nupdated vals bfore tao\n\n', self._model_params)
        self.tap_update(params)
        #print('\nupdated vals after tap:\n', self._model_params)
        self.get_initializers(self._model_params)

    def adapter(self):
        return self._adapter

    def get_initializers(self, params):
        #params_cp= params.copy()
        default_params = {'bias_param':0,  'reg': {'l1': 0, 'l2': 0}}
        backends_li = ['keras', 'pytorch']
        dist_dict = {'normal': {'mean': 0, 'stddev': 1},
                     'uniform': {'minval': 0, 'maxval': 0}}
        for backend in backends_li:# prepares a nested default config dict()
            for dist, pars in dist_dict.items():
                deep_set(default_params, ['backend', backend, 'distrib', dist], pars,
                         accessor=lambda default_params, k: default_params.setdefault(k, dict()))

        self.default_backend_dist_params = default_params
        #out_idx= 0
        for key, vals in params.items():
            sub_dict = default_params.copy()#copy.deepcopy(default_params)
            #out_idx+=1
            #rint('\n\nouter dict count', out_idx)

            if 'optimizer' not in vals.keys():
                params[key].update({'bias_param':sub_dict['bias_param'] if 'bias_param' not in vals.keys() else params[key]['bias_param']})# else params[key]['bias_param']})
                params[key].update({'bias':keras.initializers.Constant(value=params[key]['bias_param'])})#sub_dict['bias_param'] if 'bias' not in vals.keys() else params[key]['bias'])})
                if 'regularizers' not in vals.keys():#for initial call
                    params[key].update({'regularizers':sub_dict['reg']})#add default regularization
                #else:
                 #   deep_get(params, [key,])
                 #   params[key]
            if 'kernel_params' in vals.keys():
                #iner_idx=0
                custom_params = vals['kernel_params']#params[key]['kernel_params']
                #if 'bias' not in vals.keys():
                #    params[key].update({'bias':sub_dict['bias']})#add default bias
                #bias_val = sub_dict['bias']
                
                #if 'bias_param' not in vals.keys():
                #    params[key].update({'bias_param':sub_dict['bias_param']})# if 'bias_param' not in vals.keys() else pass})# else params[key]['bias_param']})
                #params[key].update({'bias':keras.initializers.Constant(value=params[key]['bias_param'])})#sub_dict['bias_param'] if 'bias' not in vals.keys() else params[key]['bias'])})
                #params[key].update({'regularizers':sub_dict['reg']})#add default regularization
                
                if 'backend' not in custom_params.keys() or 'backend' == 'keras':
                    rel_li = ['backend', 'keras', 'distrib', custom_params['distrib']
                              ] if 'distrib' in custom_params else ['backend', 'keras', 'distrib', 'normal']
                    #rel_slice = deep_get(sub_dict, rel_li).copy()
                    rel_dict = deep_get(sub_dict, rel_li).copy()#rel_slice.copy()
                    #relevant updated dictionary#contains 'distrib':'uniform if listed
                    #print('key value:', key)
                    #print('sub dict before:', sub_dict)
                    #print('rel_li:', rel_li)
                    #print('rel_dict before:', rel_dict)
                    #print('custom_params before:', custom_params)


                    rel_dict.update(custom_params)
                    #print('\nrel_dict After:', rel_dict)
                    #print('custom_params after:', custom_params)
                    #print('sub dict after:', sub_dict)
                    params[key].update({'kernel': initializers.RandomNormal(mean=rel_dict['mean'], stddev=rel_dict['stddev'])
                                        if 'normal' in rel_li else initializers.RandomUniform(minval=rel_dict['minval'], maxval=rel_dict['maxval'])})
                    #rel_dict=0

                else:#for non-keras backend
                    if not custom_params['backend'] in self.default_backend_dist_params['backend'].keys():
                        raise ValueError('Backend: {} and its distributions are not yet defined in Generalised Model'.format(
                            custom_params['backend']))

                    rel_li = ['backend', custom_params['backend'], 'distrib',
                              custom_params['distrib']]
                    rel_dict = deep_get(sub_dict, rel_li).copy()
                    rel_dict.update(custom_params)

                    params[key].update({'kernel': initializers.RandomNormal(mean=rel_dict['mean'], stddev=rel_dict['stddev']) if 'normal' in rel_li else
                                        initializers.RandomUniform(minval=rel_dict['minval'], maxval=rel_dict['maxval'])})
        self._model_params.update(params)

#    def get_initializers(self, params):
#        default_params = {}
#        backends_li = ['keras', 'pytorch']
#        dist_dict = {'normal': {'mean': 0, 'stddev': 1},
#                     'uniform': {'minval': 0, 'maxval': 0}}
#        for backend in backends_li:# prepares a nested default config dict()
#            for dist, pars in dist_dict.items():
#                deep_set(default_params, ['backend', backend, 'distrib', dist], pars,
#                         accessor=lambda default_params, k: default_params.setdefault(k, dict()))
#
#        self.default_backend_dist_params = default_params
#
#        for key, vals in params.items():
#            sub_dict = default_params.copy()
#            if 'kernel_params' in vals.keys():
#                custom_params = vals['kernel_params']
#                if 'backend' not in custom_params.keys() or 'backend' == 'keras':
#                    rel_li = ['backend', 'keras', 'distrib', custom_params['distrib']
#                              ] if 'distrib' in custom_params else ['backend', 'keras', 'distrib', 'normal']
#                    rel_dict = deep_get(sub_dict, rel_li)
#                    #relevant updated dictionary#contains 'distrib':'uniform if listed
#                    rel_dict.update(custom_params)
#                    params[key].update({'kernel': initializers.RandomNormal(mean=rel_dict['mean'], stddev=rel_dict['stddev'])
#                                        if 'normal' in rel_li else initializers.RandomUniform(minval=rel_dict['minval'], maxval=rel_dict['maxval'])})
#
#                    params[key].update({'bias': initializers.RandomNormal(mean=rel_dict['mean'], stddev=rel_dict['stddev'])
#                                        if 'normal' in rel_li else initializers.RandomUniform(minval=rel_dict['minval'], maxval=rel_dict['maxval'])})
#                else:#for non-keras backend
#                    if not custom_params['backend'] in self.default_backend_dist_params['backend'].keys():
#                        raise ValueError('Backend: {} and its distributions are not yet defined in Generalised Model'.format(
#                            custom_params['backend']))
#
#                    rel_li = ['backend', custom_params['backend'], 'distrib',
#                              custom_params['distrib']]
#                    rel_dict = deep_get(sub_dict, rel_li)
#                    rel_dict.update(custom_params)
#
#                    params[key].update({'kernel': initializers.RandomNormal(mean=rel_dict['mean'], stddev=rel_dict['stddev']) if 'normal' in rel_li else
#                                        initializers.RandomUniform(minval=rel_dict['minval'], maxval=rel_dict['maxval'])})
#
#                    params[key].update({'bias': initializers.RandomNormal(mean=rel_dict['mean'], stddev=rel_dict['stddev'])
#                                        if 'normal' in rel_li else initializers.RandomUniform(minval=rel_dict['minval'], maxval=rel_dict['maxval'])})
#        self._model_params.update(params)


@registry.register
class KerasIrt1PLModel(GeneralisedIrtModel):
    def __init__(self):
        self.adapter = IrtKerasRegressor
        self.module_name = 'mlsquare'
        self.name = 'rasch'
        self.version = 'default'

        model_params = {'ability_params': {'units': 1, 'kernel_params': {}, 'use_bias':False},
                        'diff_params': {'units': 1, 'kernel_params': {}, 'use_bias':False},
                        'disc_params': {'units': 1, 'kernel_params': {'stddev': 0}, 'train':False, 'act':'exponential', 'use_bias':False},
                        'guess_params': {'units': 1, 'kernel_params': {'distrib': 'uniform'}, 'bias_param':-3.5,'train': False, 'act': 'sigmoid', 'use_bias':True},
                        'slip_params':{'units': 1, 'kernel_params': {'distrib': 'uniform'}, 'bias_param':-3.5, 'train': False, 'act': 'sigmoid', 'use_bias':True},
                        #'regularizers': {'l1': 0, 'l2': 0},
                        'hyper_params': {'units': 1, 'optimizer': 'sgd', 'loss': 'binary_crossentropy'}}
        self.set_params(params=model_params, set_by='model_init')



@registry.register
class KerasIrt2PLModel(GeneralisedIrtModel):
    def __init__(self):
        self.adapter = IrtKerasRegressor
        self.module_name = 'mlsquare'
        self.name = 'twoPl'
        self.version = 'default'

        model_params = {'ability_params': {'units': 1, 'kernel_params': {}, 'use_bias':False},
                        'diff_params': {'units': 1, 'kernel_params': {}, 'use_bias':False},
                        'disc_params': {'units': 1, 'kernel_params': {}, 'train': True, 'act':'exponential', 'use_bias':False},
                        'guess_params': {'units': 1, 'kernel_params': {'distrib': 'uniform'}, 'bias_param':-3.5, 'train': False, 'act': 'sigmoid', 'use_bias':True},
                        'slip_params':{'units': 1, 'kernel_params': {'distrib': 'uniform'}, 'bias_param':-3.5, 'train': False, 'act': 'sigmoid', 'use_bias':True},
                        #'regularizers': {'l1': 0, 'l2': 0},
                        'hyper_params': {'units': 1, 'optimizer': 'sgd', 'loss': 'binary_crossentropy'}}

        self.set_params(params=model_params, set_by='model_init')


@registry.register
class KerasIrt3PLModel(GeneralisedIrtModel):
    def __init__(self):
        self.adapter = IrtKerasRegressor
        self.module_name = 'mlsquare'
        self.name = 'tpm'
        self.version = 'default'
        model_params = {'ability_params': {'units': 1, 'kernel_params': {}, 'use_bias':False},
                        'diff_params': {'units': 1, 'kernel_params': {},'use_bias':False},
                        'disc_params': {'units': 1, 'kernel_params': {}, 'train': True, 'act':'exponential', 'use_bias':False},
                        'guess_params': {'units': 1, 'kernel_params': {'distrib': 'uniform', 'minval': 0, 'maxval': -2.5}, 'train': True, 'act': 'sigmoid', 'use_bias':True},
                        'slip_params':{'units': 1, 'kernel_params': {'distrib': 'uniform'}, 'bias_param':-3.5, 'train': False, 'act': 'sigmoid', 'use_bias':True},
                        #'regularizers': {'l1': 0, 'l2': 0},
                        'hyper_params': {'units': 1, 'optimizer': 'sgd', 'loss': 'binary_crossentropy'}}

        self.set_params(params=model_params, set_by='model_init')
