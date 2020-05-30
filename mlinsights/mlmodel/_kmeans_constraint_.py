# -*- coding: utf-8 -*-
"""
@file
@brief Implémente la classe @see cl ConstraintKMeans.
"""
import bisect
from pandas import DataFrame
import numpy
import scipy.sparse
from scipy.spatial.distance import cdist
from sklearn.metrics.pairwise import euclidean_distances
from sklearn.utils.extmath import row_norms
from ._kmeans_022 import (
    _centers_dense, _centers_sparse,
    _labels_inertia_skl
)


def linearize_matrix(mat, *adds):
    """
    Linearizes a matrix into a new one
    with 3 columns value, row, column.
    The output format is similar to
    :epkg:`csr_matrix` but null values are kept.

    @param      mat     matrix
    @param      adds    additional square matrices
    @return             new matrix

    *adds* defines additional matrices, it adds
    columns on the right side and fill them with
    the corresponding value taken into the additional
    matrices.
    """
    if scipy.sparse.issparse(mat):
        if isinstance(mat, scipy.sparse.csr_matrix):
            max_row = mat.shape[0]
            res = numpy.empty((len(mat.data), 3 + len(adds)), dtype=mat.dtype)
            row = 0
            for i, v in enumerate(mat.data):
                while row < max_row and i >= mat.indptr[row]:
                    row += 1
                res[i, 0] = v
                a, b = row - 1, mat.indices[i]
                res[i, 1] = a
                res[i, 2] = b
                for k, am in enumerate(adds):
                    res[i, k + 3] = am[a, b]
            return res
        raise NotImplementedError(
            "This kind of sparse matrix is not handled: {0}".format(type(mat)))
    else:
        n = mat.shape[0]
        c = mat.shape[1]
        ic = numpy.arange(mat.shape[1])
        res = numpy.empty((n * c, 3 + len(adds)), dtype=mat.dtype)
        for i in range(0, n):
            a = i * c
            b = (i + 1) * c
            res[a:b, 1] = i
            res[a:b, 2] = ic
        res[:, 0] = mat.ravel()
        for k, am in enumerate(adds):
            res[:, 3 + k] = am.ravel()
        return res


def constraint_kmeans(X, labels, sample_weight, centers, inertia,
                      iter, max_iter,  # pylint: disable=W0622
                      strategy='gain', verbose=0, state=None,
                      learning_rate=1., fLOG=None):
    """
    Completes the constraint :epkg:`k-means`.

    @param      X               features
    @param      labels          initialized labels (unused)
    @param      sample_weight   sample weight
    @param      centers         initialized centers
    @param      inertia         initialized inertia (unused)
    @param      iter            number of iteration already done
    @param      max_iter        maximum of number of iteration
    @param      strategy        strategy used to sort observations before
                                mapping them to clusters
    @param      verbose         verbose
    @param      state           random state
    @param      learning_rate   used by strategy `'weights'`
    @param      fLOG            logging function (needs to be specified otherwise
                                verbose has no effects)
    @return                     tuple (best_labels, best_centers, best_inertia, iter)
    """
    if labels.dtype != numpy.int32:
        raise TypeError(
            "Labels must be an array of int not '{0}'".format(labels.dtype))

    if strategy == 'weights':
        return _constraint_kmeans_weights(
            X, labels, sample_weight, centers, inertia, iter,
            max_iter, verbose=verbose, state=state,
            learning_rate=learning_rate, fLOG=fLOG)
    else:
        if isinstance(X, DataFrame):
            X = X.values
        x_squared_norms = row_norms(X, squared=True)
        counters = numpy.empty((centers.shape[0],), dtype=numpy.int32)
        limit = X.shape[0] // centers.shape[0]
        leftover = X.shape[0] - limit * centers.shape[0]
        leftclose = numpy.empty((centers.shape[0],), dtype=numpy.int32)
        n_clusters = centers.shape[0]
        distances_close = numpy.empty((X.shape[0],), dtype=X.dtype)
        best_inertia = None
        prev_labels = None
        best_iter = None

        # association
        _constraint_association(leftover, counters, labels, leftclose, distances_close,
                                centers, X, x_squared_norms, limit, strategy, state=state)

        if sample_weight is None:
            sw = numpy.ones((X.shape[0],))
        else:
            sw = sample_weight

        if scipy.sparse.issparse(X):
            _centers_fct = _centers_sparse
        else:
            _centers_fct = _centers_dense

        while iter < max_iter:

            # compute new clusters
            centers = _centers_fct(
                X, sw, labels, n_clusters, distances_close)

            # association
            _constraint_association(
                leftover, counters, labels, leftclose, distances_close,
                centers, X, x_squared_norms, limit, strategy, state=state)

            # inertia
            _, inertia = _labels_inertia_skl(
                X=X, sample_weight=sw, x_squared_norms=x_squared_norms,
                centers=centers, distances=distances_close)

            iter += 1
            if verbose and fLOG:
                fLOG("CKMeans %d/%d inertia=%f" % (iter, max_iter, inertia))

            # best option so far?
            if best_inertia is None or inertia < best_inertia:
                best_inertia = inertia
                best_centers = centers.copy()
                best_labels = labels.copy()
                best_iter = iter

            # early stop
            if (best_inertia is not None and inertia >= best_inertia and
                    iter > best_iter + 5):
                break
            if prev_labels is not None and numpy.array_equal(prev_labels, labels):
                break
            prev_labels = labels.copy()

        return best_labels, best_centers, best_inertia, None, iter


def constraint_predictions(X, centers, strategy, state=None):
    """
    Computes the predictions but tries
    to associates the same numbers of points
    in each cluster.

    @param      X           features
    @param      centers     centers of each clusters
    @param      strategy    strategy used to sort point before
                            mapping them to a cluster
    @param      state       random state
    @return                 labels, distances, distances_close
    """
    if isinstance(X, DataFrame):
        X = X.values
    x_squared_norms = row_norms(X, squared=True)
    counters = numpy.empty((centers.shape[0],), dtype=numpy.int32)
    limit = X.shape[0] // centers.shape[0]
    leftover = X.shape[0] - limit * centers.shape[0]
    leftclose = numpy.empty((centers.shape[0],), dtype=numpy.int32)
    distances_close = numpy.empty((X.shape[0],), dtype=X.dtype)
    labels = numpy.empty((X.shape[0],), dtype=numpy.int32)

    distances = _constraint_association(
        leftover, counters, labels, leftclose,
        distances_close, centers, X, x_squared_norms,
        limit, strategy, state=state)

    return labels, distances, distances_close


def _constraint_association(leftover, counters, labels, leftclose, distances_close,
                            centers, X, x_squared_norms, limit, strategy, state=None):
    """
    Completes the constraint :epkg:`k-means`.

    @param      X               features
    @param      labels          initialized labels (unused)
    @param      centers         initialized centers
    @param      x_squared_norms norm of *X*
    @param      limit           number of point to associate per cluster
    @param      leftover        number of points to associate at the end
    @param      counters        allocated array
    @param      leftclose       allocated array
    @param      labels          allocated array
    @param      distances_close allocated array
    @param      strategy        strategy used to sort point before
                                mapping them to a cluster
    @param      state           random state
    """
    if strategy in ('distance', 'distance_p'):
        return _constraint_association_distance(
            leftover, counters, labels, leftclose, distances_close,
            centers, X, x_squared_norms, limit, strategy, state=state)
    if strategy in ('gain', 'gain_p'):
        return _constraint_association_gain(
            leftover, counters, labels, leftclose, distances_close,
            centers, X, x_squared_norms, limit, strategy, state=state)
    raise ValueError("Unknwon strategy '{0}'.".format(strategy))


def _constraint_association_distance(leftover, counters, labels, leftclose, distances_close,
                                     centers, X, x_squared_norms, limit, strategy, state=None):
    """
    Completes the constraint *k-means*,
    the function sorts points by distance to the closest
    cluster and associates them into that order.

    @param      X               features
    @param      labels          initialized labels (unused)
    @param      centers         initialized centers
    @param      x_squared_norms norm of *X*
    @param      limit           number of point to associate per cluster
    @param      leftover        number of points to associate at the end
    @param      counters        allocated array
    @param      leftclose       allocated array
    @param      labels          allocated array
    @param      distances_close allocated array
    @param      strategy        strategy used to sort point before
                                mapping them to a cluster
    @param      state           random state (unused)
    """

    # initialisation
    counters[:] = 0
    leftclose[:] = -1
    distances_close[:] = numpy.nan
    labels[:] = -1

    # distances
    distances = euclidean_distances(
        centers, X, Y_norm_squared=x_squared_norms, squared=True)
    distances = distances.T

    strategy_coef = _compute_strategy_coefficient(distances, strategy, labels)
    distance_linear = linearize_matrix(distances, strategy_coef)
    sorted_distances = distance_linear[distance_linear[:, 3].argsort()]

    nover = leftover
    for i in range(0, sorted_distances.shape[0]):
        ind = int(sorted_distances[i, 1])
        if labels[ind] >= 0:
            continue
        c = int(sorted_distances[i, 2])
        if counters[c] < limit:
            # The cluster still accepts new points.
            counters[c] += 1
            labels[ind] = c
            distances_close[ind] = sorted_distances[i, 0]
        elif nover > 0 and leftclose[c] == -1:
            # The cluster may accept one point if the number
            # of clusters does not divide the number of points in X.
            counters[c] += 1
            labels[ind] = c
            nover -= 1
            leftclose[c] = 0
            distances_close[ind] = sorted_distances[i, 0]
    return distances


def _compute_strategy_coefficient(distances, strategy, labels):
    """
    Creates a matrix
    """
    if strategy in ('distance', 'distance_p'):
        return distances
    if strategy in ('gain', 'gain_p'):
        ar = numpy.arange(distances.shape[0])
        dist = distances[ar, labels]
        return distances - dist[:, numpy.newaxis]
    raise ValueError("Unknwon strategy '{0}'.".format(strategy))


def _constraint_association_gain(leftover, counters, labels, leftclose, distances_close,
                                 centers, X, x_squared_norms, limit, strategy, state=None):
    """
    Completes the constraint *k-means*.
    Follows the method described in `Same-size k-Means Variation
    <https://elki-project.github.io/tutorial/same-size_k_means>`_.

    @param      X               features
    @param      labels          initialized labels (unused)
    @param      centers         initialized centers
    @param      x_squared_norms norm of *X*
    @param      limit           number of points to associate per cluster
    @param      leftover        number of points to associate at the end
    @param      counters        allocated array
    @param      leftclose       allocated array
    @param      labels          allocated array
    @param      distances_close allocated array
    @param      strategy        strategy used to sort point before
                                mapping them to a cluster
    @param      state           random state

    See `Same-size k-Means Variation <https://elki-project.github.io/tutorial/same-size_k_means>`_.
    """
    # distances
    distances = euclidean_distances(
        centers, X, Y_norm_squared=x_squared_norms, squared=True)
    distances = distances.T

    if strategy == 'gain_p':
        labels[:] = numpy.argmin(distances, axis=1)
    else:
        # We assume labels comes from a previous iteration.
        pass

    strategy_coef = _compute_strategy_coefficient(distances, strategy, labels)
    distance_linear = linearize_matrix(distances, strategy_coef)
    sorted_distances = distance_linear[distance_linear[:, 3].argsort()]
    distances_close[:] = 0

    # counters
    ave = limit
    counters[:] = 0
    for i in labels:
        counters[i] += 1
    leftclose[:] = counters[:] - ave
    leftclose[leftclose < 0] = 0
    nover = X.shape[0] - ave * counters.shape[0]
    sumi = nover - leftclose.sum()
    if sumi != 0:
        if state is None:
            state = numpy.random.RandomState()  # pylint: disable=E1101

        def loopf(h, sumi):
            if sumi < 0 and leftclose[h] > 0:  # pylint: disable=R1716
                sumi -= leftclose[h]
                leftclose[h] = 0
            elif sumi > 0 and leftclose[h] == 0:
                leftclose[h] = 1
                sumi += 1
            return sumi

        it = 0
        while sumi != 0:
            h = state.randint(0, counters.shape[0])
            sumi = loopf(h, sumi)
            it += 1
            if it > counters.shape[0] * 2:
                break
        for h in range(counters.shape[0]):
            if sumi == 0:
                break
            sumi = loopf(h, sumi)

    transfer = {}

    for i in range(0, sorted_distances.shape[0]):
        gain = sorted_distances[i, 3]
        ind = int(sorted_distances[i, 1])
        dest = int(sorted_distances[i, 2])
        cur = labels[ind]
        if distances_close[ind]:
            continue
        if cur == dest:
            continue
        if ((counters[dest] < ave + leftclose[dest]) and
                (counters[cur] > ave + leftclose[cur])):
            labels[ind] = dest
            counters[cur] -= 1
            counters[dest] += 1
            distances_close[ind] = 1  # moved
        else:
            cp = transfer.get((dest, cur), [])
            while len(cp) > 0:
                g, destind = cp[0]
                if distances_close[destind]:
                    del cp[0]
                else:
                    break
            if len(cp) > 0:
                g, destind = cp[0]
                if g + gain < 0:
                    del cp[0]
                    labels[ind] = dest
                    labels[destind] = cur
                    add = False
                    distances_close[ind] = 1  # moved
                    distances_close[destind] = 1  # moved
                else:
                    add = True
            else:
                add = True
            if add:
                # We add the point to the list of points willing to transfer.
                if (cur, dest) not in transfer:
                    transfer[cur, dest] = []
                gain = sorted_distances[i, 3]
                bisect.insort(transfer[cur, dest], (gain, ind))

    distances_close[:] = distances[numpy.arange(X.shape[0]), labels]

    neg = (counters < ave).sum()
    if neg > 0:
        raise RuntimeError(
            "The algorithm failed, counters={0}".format(counters))

    return distances


def _constraint_kmeans_weights(X, labels, sample_weight, centers, inertia, iter,
                               max_iter, verbose=0, state=None, learning_rate=1.,
                               fLOG=None):
    """
    Runs KMeans iterator but weights cluster among them.

    @param      X               features
    @param      labels          initialized labels (unused)
    @param      sample_weight   sample weight
    @param      centers         initialized centers
    @param      inertia         initialized inertia (unused)
    @param      iter            number of iteration already done
    @param      max_iter        maximum of number of iteration
    @param      verbose         verbose
    @param      state           random state
    @param      learning_rate   learning rate
    @param      fLOG            logging function (needs to be specified otherwise
                                verbose has no effects)
    @return                     tuple (best_labels, best_centers, best_inertia, weights, it)
    """
    if isinstance(X, DataFrame):
        X = X.values
    n_clusters = centers.shape[0]
    best_inertia = None
    prev_labels = None
    best_iter = None
    weights = numpy.ones(centers.shape[0])
    if sample_weight is None:
        sw = numpy.ones((X.shape[0],))
    else:
        sw = sample_weight

    if scipy.sparse.issparse(X):
        _centers_fct = _centers_sparse
    else:
        _centers_fct = _centers_dense

    total_inertia = _inertia(X, sw)

    it = 0
    while it < max_iter:

        # compute new clusters
        centers = _centers_fct(
            X, sw, labels, n_clusters, None)

        # association
        labels = _constraint_association_weights(X, centers, sw, weights)
        if len(set(labels)) != centers.shape[0]:
            if verbose and fLOG:
                if isinstance(verbose, int) and verbose >= 10:
                    fLOG("CKMeans new weights: w=%r" % weights)
                else:
                    fLOG("CKMeans new weights")
            weights[:] = 1
            labels = _constraint_association_weights(X, centers, sw, weights)

        # inertia
        inertia, diff = _labels_inertia_weights(
            X, centers, sw, weights, labels, total_inertia)
        if numpy.isnan(inertia):
            raise RuntimeError(  # pragma: no cover
                "nanNobs={} Nclus={}\ninertia={}\nweights={}\ndiff={}\nlabels={}".format(
                    X.shape[0], centers.shape[0], inertia, weights, diff,
                    set(labels)))

        # best option so far?
        if best_inertia is None or inertia < best_inertia:
            best_inertia = inertia
            best_centers = centers.copy()
            best_labels = labels.copy()
            best_weights = weights.copy()
            best_iter = it

        # moves weights
        weights, hist = _adjust_weights(X, sw, weights, labels,
                                        learning_rate / (it + 10))

        it += 1
        if verbose and fLOG:
            if isinstance(verbose, int) and verbose >= 10:
                fLOG("CKMeans %d/%d inertia=%f (%f T=%f) dw=%r w=%r" % (
                    it, max_iter, inertia, best_inertia, total_inertia,
                    diff, weights))
            else:
                fLOG("CKMeans %d/%d inertia=%f (%f T=%f)" % (
                    it, max_iter, inertia, best_inertia, total_inertia))

        # early stop
        if (best_inertia is not None and inertia >= best_inertia and
                it > best_iter + 5 and numpy.abs(diff).sum() <= weights.shape[0] / 2):
            break
        prev_labels = labels.copy()

    return best_labels, best_centers, best_inertia, best_weights, it


def _constraint_association_weights(X, centers, sw, weights):
    """
    Associates points to clusters.

    @param      X           features
    @param      centers     centers
    @param      sw          sample weights
    @param      weights     cluster weights
    @return                 labels
    """
    dist = cdist(X, centers) * weights.reshape((1, -1))
    index = numpy.argmin(dist, axis=1)
    return index


def _inertia(X, sw):
    """
    Computes total weighted inertia.

    @param      X               features
    @param      sw              sample weights
    @return                     inertia
    """
    bary = numpy.mean(X, axis=0)
    diff = X - bary
    norm = numpy.linalg.norm(diff, axis=1)
    if sw is not None:
        norm *= sw
    return sw.sum()


def _labels_inertia_weights(X, centers, sw, weights, labels, total_inertia):
    """
    Computes weighted inertia. It also adds a fraction
    of the whole inertia depending on how balanced the
    clusters are.

    @param      X               features
    @param      centers         centers
    @param      sw              sample weights
    @param      weights         cluster weights
    @param      labels          labels
    @param      total_inertia   total inertia
    @return                     inertia
    """
    www, exp, N = _compute_balance(X, sw, labels, centers.shape[0])
    www -= exp
    wwwa = numpy.abs(www)
    ratio = wwwa.sum() / X.shape[0]
    dist = cdist(X, centers) * weights.reshape((1, -1)) * sw.reshape((-1, 1))
    return dist.sum() + ratio * total_inertia, www


def _compute_balance(X, sw, labels, nbc=None):
    """
    Computes weights difference.

    @param      X           features
    @param      sw          sample weights
    @param      labels      known labels
    @param      nbc         number of clusters
    @return                 (weights per cluster, expected weight,
                            total weight)
    """
    if nbc is None:
        nbc = labels.max() + 1
    N = numpy.float64(nbc)
    www = numpy.zeros((nbc, ), dtype=numpy.float64)
    if sw is None:
        for la in labels:
            www[la] += 1
    else:
        for w, la in zip(sw, labels):
            www[la] += w
    exp = www.sum() / N
    return www, exp, N


def _adjust_weights(X, sw, weights, labels, lr):
    """
    Changes *weights* mapped to every cluster.
    *weights < 1* are used for big clusters,
    *weights > 1* are used for small clusters.

    @param      X           features
    @param      centers     centers
    @param      sw          sample weights
    @param      weights     cluster weights
    @param      lr          learning rate
    @param      labels      known labels
    @return                 labels
    """
    www, exp, N = _compute_balance(X, sw, labels, weights.shape[0])

    for i in range(0, weights.shape[0]):
        w = weights[i]
        nw = (www[i] - exp) / exp
        delta = nw * lr
        weights[i] += delta
        N += delta

    return weights / N * weights.shape[0], www