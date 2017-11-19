"""
@file
@brief Implements a way to get close examples based
on the output of a machine learned model.
"""
from sklearn.neighbors import NearestNeighbors
from ..featurizers import model_featurizer
from ..helpers.parameters import format_function_call
from .search_engine_vectors import SearchEngineVectors


class SearchEnginePredictions(SearchEngineVectors):
    """
    Extends class @see cl SearchEngineVectors by
    looking for neighbors to a vector *X* by
    looking neighbors to *f(X)* and not *X*.
    *f* can be any function which converts a vector
    into another one or a machine learned model.
    In that case, *f* will be set to a default behavior.
    See function @see fn model_featurizer.
    """

    def __init__(self, fct, **knn):
        """
        @param      fct         function *f* applied before looking for neighbors,
                                it can also be a machine learned model
        @param      pknn        list of parameters, see :epkg:`sklearn:neighborsNearestNeighbors`
        """
        super().__init__(**knn)
        if callable(fct):
            self.fct = fct
        else:
            self.fct = model_featurizer(fct)
        self._fct_init = fct

    def __repr__(self):
        """
        usual
        """
        if self.pknn:
            pp = self.pknn.copy()
        else:
            pp = {}
        pp['fct'] = self._fct_init
        return format_function_call(self.__class__.__name__, pp)

    def fit(self, data=None, features=None, metadata=None):
        """
        Every vector comes with a list of metadata.

        @param      data        a dataframe or None if the
                                the features and the metadata
                                are specified with an array and a
                                dictionary
        @param      features    features columns  or
                                or an array
        @param      metadata    data
        """
        self._prepare_fit(data=data, features=features, metadata=metadata)
        if isinstance(self.features_, list):
            raise TypeError(
                "features_ cannot be a list when training the model.")
        self.features_ = self.fct(self.features_)
        self.knn_ = NearestNeighbors(**self.pknn)
        self.knn_.fit(self.features_)

    def kneighbors(self, X, n_neighbors=None):
        """
        Searches for neighbors close to *X*.

        @param      X               features
        @return                     score, ind, meta

        *score* is an array representing the lengths to points,
        *ind* contains the indices of the nearest points in the population matrix,
        *meta* is the metadata
        """
        xp = self.fct(X)
        if len(xp.shape) == 1:
            xp = xp.reshape((1, len(xp)))
        return super().kneighbors(xp, n_neighbors=n_neighbors)