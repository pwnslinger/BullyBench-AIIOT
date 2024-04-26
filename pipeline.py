import nltk
import os
import sys
import warnings
import numpy as np
import pandas as pd
import multiprocessing as mp

from datetime import datetime
from sanitizer import clean_tweets
from scipy.sparse.csr import csr_matrix
from nltk.stem import WordNetLemmatizer
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.model_selection import train_test_split
from sklearn.naive_bayes import GaussianNB, MultinomialNB
from sklearn.svm import SVC
from sklearn.neural_network import MLPClassifier
from sklearn.ensemble import AdaBoostClassifier
from sklearn.tree import DecisionTreeClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GridSearchCV
from sklearn.exceptions import UndefinedMetricWarning
from report import Writer
from sklearn.pipeline import Pipeline
from sklearn.exceptions import NotFittedError, ConvergenceWarning
from embedding import MeanEmbeddingVectorizer, TfidfEmbeddingVectorizer, FastTextVectorizer, TfidfVectorizerStub

warnings.filterwarnings("ignore", category=UndefinedMetricWarning)
warnings.filterwarnings("ignore", category=ConvergenceWarning)
np.seterr(divide='ignore', invalid='ignore')
pd.options.display.max_colwidth = 200

lemmatizer = WordNetLemmatizer()

nltk.download('stopwords')

REPORT_SUFFIX = "_%s.csv" % datetime.now().strftime("%m-%d-%Y")

class Classifier(object):
    def __init__(self, model, param):
        self.model = model
        self.param = param
        self.gs = GridSearchCV(self.model, self.param, cv=5, error_score=0,
                               refit=True, verbose=0)

    def fit(self, X, y = None):
        # MultinomialNB cannot process negative values in embedding, scale to [0,1]
        if self.model.__class__.__name__ == 'MultinomialNB':
            if (isinstance(X, csr_matrix) and any((X<0).indptr !=0 )) or \
                    (isinstance(X, np.ndarray) and (X < 0).any()):
                print('MultinomialNB with negative values in embedding feature vector detected!')
                return
        else:
            return self.gs.fit(X, y)

    def predict(self, X):
        return self.gs.predict(X)

class VectorizerMixin(TransformerMixin, BaseEstimator):
    def __init__(self, model, data):
        self.model = model(data)

    def fit(self, X, y = None):
        return self.model.fit(X, y)

    def transform(self, X, y = None):
        return self.model.transform(X)

clf_models = {
    #'MultinomialNB': MultinomialNB(),
    'NaiveBayes': GaussianNB(),
    'SVC': SVC(),
    'DecisionTree': DecisionTreeClassifier(),
    'MLPClassifier': MLPClassifier(),
    'RandomForest': RandomForestClassifier(),
    'GradientBoosting': GradientBoostingClassifier(),
    'LogisticRegression': LogisticRegression(),
    'AdaBoost': AdaBoostClassifier(),
}

clf_params = {
    #'MultinomialNB': { 'alpha': [0.5, 1], 'fit_prior': [True, False] },
    'NaiveBayes': {},
    'SVC': { 'kernel': ['linear'] },
    'DecisionTree': { 'min_samples_split': [2, 5] },
    'MLPClassifier': { 'alpha': [0.001, 0.05], 'activation': ['tanh', 'relu'],
                   'solver': ['sgd', 'adam'], 'hidden_layer_sizes': [(150,50,100), (50,50,50)],
                      'learning_rate': ['constant', 'adaptive']},
    'GradientBoosting': { 'learning_rate': [0.05, 0.1], 'min_samples_split': [2, 5] },
    'LogisticRegression': { 'max_iter': [700, 800] },
    'AdaBoost': {},
    'RandomForest': {}
}

vec_cls = {
    'Word2Vec': MeanEmbeddingVectorizer,
    'Word2Vec-TFIDF': TfidfEmbeddingVectorizer,
    'Tfidf': TfidfVectorizerStub,
    'FastText': FastTextVectorizer
}

def exec_pipeline(vec_method, clf_method, data, q):

    print("%s Classifier with %s Vectorizer Started!"%(clf_method, vec_method))

    X_train, X_test, y_train, y_test = train_test_split(data['text'].values.ravel(),
                                                        data['label'].values.ravel(),
                                                        test_size=0.3, shuffle=True)

    clf = Pipeline([(vec_method, VectorizerMixin(vec_cls[vec_method], data)),
                    ('Classifier', Classifier(clf_models[clf_method],
                                              clf_params[clf_method]))])

    clf.fit(X_train, y_train)
    try:
        y_pred = clf.predict(X_test)
    except NotFittedError:
        return

    # generate all plots
    ds_name = os.path.basename(fname).split('.')[0]
    writer = Writer(ds_name, y_test, y_pred, clf_method, vec_method)
    writer.generate_plots()

    # generate CSV report
    best_estimator = clf.steps[1][1].gs.best_estimator_
    result = writer.classification_report(best_estimator)
    q.put(result)

def listener(q):
    report_path = os.path.join('results', REPORT_NAME)
    with open(report_path, 'w') as f:
        while True:
            msg = q.get()
            if msg == 'done':
                break
            f.write(str(msg) + '\n')
            f.flush()

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: %s dataset.csv'%__name__)
        sys.exit('not enough arguments provided!')

    fname = sys.argv[1]
    REPORT_NAME = os.path.basename(fname).split('.')[0] + REPORT_SUFFIX

    if os.path.exists(fname):
        data = pd.read_csv(os.path.join(os.getcwd(), fname), encoding='utf-8')
        data['label'] = data['label'].apply(lambda label: 0 if label == False else 1)

        # clean the tweets
        clean_tweets(data, exp_flag=True)

        #must use Manager queue here, or will not work
        manager = mp.Manager()
        q = manager.Queue()
        pool = mp.Pool(mp.cpu_count()//2 + 2)

        #put listener to work first
        watcher = pool.apply_async(listener, (q,))

        jobs = []

        for clf_method in clf_models.keys():
            for vec_method in vec_cls.keys():
                job = pool.apply_async(exec_pipeline, (vec_method, clf_method,
                                                        data, q, ))
                jobs.append(job)

        for job in jobs:
            job.get()

        q.put('done')
        pool.close()
        pool.join()
