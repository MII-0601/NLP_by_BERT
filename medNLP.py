
# %%
import codecs
from bs4 import BeautifulSoup
with codecs.open("MedTxt-CR-JA-training.xml", "r", "utf-8") as file:
    soup = BeautifulSoup(file, "html.parser")


# %%

frequent_tags= ['d', 'a', 'timex3', 't-test', 't-key', 't-val', 'm-key', 'm-val']
tags_value = [int(i) for i in range(1, len(frequent_tags)+1)]
dict_tags = dict(zip(frequent_tags, tags_value))

def entities_from_xml(file_name):
    import codecs
    from bs4 import BeautifulSoup
    with codecs.open(file_name, "r", "utf-8") as file:
        soup = BeautifulSoup(file, "html.parser")

    for elem_articles in soup.find_all("articles"):#articles内のarticleを一つずつ取り出す
        entities = []
        articles = []
        for elem in elem_articles.find_all('article'):#article内の要素を一つずつ取り出す
            entities_article = []
            text_list = []
            pos1 = 0
            pos2 = 0
            for child in elem:#取り出した要素に対して，一つずつ処理する
                #（タグのないものについても要素として取得されるので，位置(pos)はずれない）
                text = child.string

                pos2 += len(child.string)
                if child.name in frequent_tags:#特定のタグについて，固有表現の表現形，位置，タグを取得
                    entities_article.append({'name':child.string, 'span':[pos1, pos2], 'type_id':dict_tags[child.name]})
                pos1 = pos2
                text_list.append(text)
            articles.append("".join(text_list))
            entities.append(entities_article) 
    return articles, entities
    #return {'text':article.string, 'entities':entities}

    
# %%
articles = entities_from_xml('MedTxt-CR-JA-training.xml')[0]
entities = entities_from_xml('MedTxt-CR-JA-training.xml')[1]

# %%
entities[0]
# %%
dataset = []
for i in range(len(articles)):
    dataset.append({'text': articles[i],\
    'entities': entities[i]})
# %%
dataset[1]
# %%
# 8-3
import itertools
import random
import json
from tqdm import tqdm
import numpy as np
import unicodedata

import torch
from torch.utils.data import DataLoader
from transformers import BertJapaneseTokenizer, BertForTokenClassification
import pytorch_lightning as pl

# 日本語学習済みモデル
MODEL_NAME = 'cl-tohoku/bert-base-japanese-whole-word-masking'

# %%
# 8-5
# 8-21
class NER_tokenizer_BIO(BertJapaneseTokenizer):

    # 初期化時に固有表現のカテゴリーの数`num_entity_type`を
    # 受け入れるようにする。
    def __init__(self, *args, **kwargs):
        self.num_entity_type = kwargs.pop('num_entity_type')
        super().__init__(*args, **kwargs)

    def encode_plus_tagged(self, text, entities, max_length):
        """
        文章とそれに含まれる固有表現が与えられた時に、
        符号化とラベル列の作成を行う。
        """
        # 固有表現の前後でtextを分割し、それぞれのラベルをつけておく。
        splitted = [] # 分割後の文字列を追加していく
        position = 0
        for entity in entities:
            start = entity['span'][0]
            end = entity['span'][1]
            label = entity['type_id']
            splitted.append({'text':text[position:start], 'label':0})
            splitted.append({'text':text[start:end], 'label':label})
            position = end
        splitted.append({'text': text[position:], 'label':0})
        splitted = [ s for s in splitted if s['text'] ]

        # 分割されたそれぞれの文章をトークン化し、ラベルをつける。
        tokens = [] # トークンを追加していく
        labels = [] # ラベルを追加していく
        for s in splitted:
            tokens_splitted = self.tokenize(s['text'])
            label = s['label']
            if label > 0: # 固有表現
                # まずトークン全てにI-タグを付与
                labels_splitted =  \
                    [ label + self.num_entity_type ] * len(tokens_splitted)
                # 先頭のトークンをB-タグにする
                labels_splitted[0] = label
            else: # それ以外
                labels_splitted =  [0] * len(tokens_splitted)
            
            tokens.extend(tokens_splitted)
            labels.extend(labels_splitted)

        # 符号化を行いBERTに入力できる形式にする。
        input_ids = self.convert_tokens_to_ids(tokens)
        encoding = self.prepare_for_model(
            input_ids, 
            max_length=max_length, 
            padding='max_length',
            truncation=True
        ) 

        # ラベルに特殊トークンを追加
        labels = [0] + labels[:max_length-2] + [0]
        labels = labels + [0]*( max_length - len(labels) )
        encoding['labels'] = labels

        return encoding

    def encode_plus_untagged(
        self, text, max_length=None, return_tensors=None
    ):
        """
        文章をトークン化し、それぞれのトークンの文章中の位置も特定しておく。
        IO法のトークナイザのencode_plus_untaggedと同じ
        """
        # 文章のトークン化を行い、
        # それぞれのトークンと文章中の文字列を対応づける。
        tokens = [] # トークンを追加していく。
        tokens_original = [] # トークンに対応する文章中の文字列を追加していく。
        words = self.word_tokenizer.tokenize(text) # MeCabで単語に分割
        for word in words:
            # 単語をサブワードに分割
            tokens_word = self.subword_tokenizer.tokenize(word) 
            tokens.extend(tokens_word)
            if tokens_word[0] == '[UNK]': # 未知語への対応
                tokens_original.append(word)
            else:
                tokens_original.extend([
                    token.replace('##','') for token in tokens_word
                ])

        # 各トークンの文章中での位置を調べる。（空白の位置を考慮する）
        position = 0
        spans = [] # トークンの位置を追加していく。
        for token in tokens_original:
            l = len(token)
            while 1:
                if token != text[position:position+l]:
                    position += 1
                else:
                    spans.append([position, position+l])
                    position += l
                    break

        # 符号化を行いBERTに入力できる形式にする。
        input_ids = self.convert_tokens_to_ids(tokens) 
        encoding = self.prepare_for_model(
            input_ids, 
            max_length=max_length, 
            padding='max_length' if max_length else False, 
            truncation=True if max_length else False
        )
        sequence_length = len(encoding['input_ids'])
        # 特殊トークン[CLS]に対するダミーのspanを追加。
        spans = [[-1, -1]] + spans[:sequence_length-2] 
        # 特殊トークン[SEP]、[PAD]に対するダミーのspanを追加。
        spans = spans + [[-1, -1]] * ( sequence_length - len(spans) ) 

        # 必要に応じてtorch.Tensorにする。
        if return_tensors == 'pt':
            encoding = { k: torch.tensor([v]) for k, v in encoding.items() }

        return encoding, spans

    @staticmethod
    def Viterbi(scores_bert, num_entity_type, penalty=10000):
        """
        Viterbiアルゴリズムで最適解を求める。
        """
        m = 2*num_entity_type + 1
        penalty_matrix = np.zeros([m, m])
        for i in range(m):
            for j in range(1+num_entity_type, m):
                if not ( (i == j) or (i+num_entity_type == j) ): 
                    penalty_matrix[i,j] = penalty
        
        path = [ [i] for i in range(m) ]
        scores_path = scores_bert[0] - penalty_matrix[0,:]
        scores_bert = scores_bert[1:]

        for scores in scores_bert:
            assert len(scores) == 2*num_entity_type + 1
            score_matrix = np.array(scores_path).reshape(-1,1) \
                + np.array(scores).reshape(1,-1) \
                - penalty_matrix
            scores_path = score_matrix.max(axis=0)
            argmax = score_matrix.argmax(axis=0)
            path_new = []
            for i, idx in enumerate(argmax):
                path_new.append( path[idx] + [i] )
            path = path_new

        labels_optimal = path[np.argmax(scores_path)]
        return labels_optimal

    def convert_bert_output_to_entities(self, text, scores, spans):
        """
        文章、分類スコア、各トークンの位置から固有表現を得る。
        分類スコアはサイズが（系列長、ラベル数）の2次元配列
        """
        assert len(spans) == len(scores)
        num_entity_type = self.num_entity_type
        
        # 特殊トークンに対応する部分を取り除く
        scores = [score for score, span in zip(scores, spans) if span[0]!=-1]
        spans = [span for span in spans if span[0]!=-1]

        # Viterbiアルゴリズムでラベルの予測値を決める。
        labels = self.Viterbi(scores, num_entity_type)

        # 同じラベルが連続するトークンをまとめて、固有表現を抽出する。
        entities = []
        for label, group \
            in itertools.groupby(enumerate(labels), key=lambda x: x[1]):
            
            group = list(group)
            start = spans[group[0][0]][0]
            end = spans[group[-1][0]][1]

            if label != 0: # 固有表現であれば
                if 1 <= label <= num_entity_type:
                     # ラベルが`B-`ならば、新しいentityを追加
                    entity = {
                        "name": text[start:end],
                        "span": [start, end],
                        "type_id": label
                    }
                    entities.append(entity)
                else:
                    # ラベルが`I-`ならば、直近のentityを更新
                    entity['span'][1] = end 
                    entity['name'] = text[entity['span'][0]:entity['span'][1]]
                
        return entities

# %%
tokenizer = NER_tokenizer_BIO.from_pretrained(MODEL_NAME, num_entity_type=len(frequent_tags))

# %%
text = '昨日のみらい事務所との打ち合わせは順調だった。'
entities = [
    {'name': 'みらい事務所', 'span': [3,9], 'type_id': 1}
]

encoding = tokenizer.encode_plus_tagged(
    text, entities, max_length=20
)
print(encoding)
# %%
encoding = tokenizer.encode_plus_tagged(
    dataset[0]['text'], dataset[0]['entities'], max_length=512
)
print(encoding)

# %%
# データセットの分割
random.shuffle(dataset)
n = len(dataset)
n_train = int(n*0.6)
n_val = int(n*0.2)
dataset_train = dataset[:n_train]
dataset_val = dataset[n_train:n_train+n_val]
dataset_test = dataset[n_train+n_val:]
# %%
# 8-15
def create_dataset(tokenizer, dataset, max_length):
    """
    データセットをデータローダに入力できる形に整形。
    """
    dataset_for_loader = []
    for sample in dataset:
        text = sample['text']
        entities = sample['entities']
        encoding = tokenizer.encode_plus_tagged(
            text, entities, max_length=max_length
        )
        encoding = { k: torch.tensor(v) for k, v in encoding.items() }
        dataset_for_loader.append(encoding)
    return dataset_for_loader

# トークナイザのロード
tokenizer = NER_tokenizer.from_pretrained(MODEL_NAME)

# データセットの作成
max_length = 128
dataset_train_for_loader = create_dataset(
    tokenizer, dataset_train, max_length
)
dataset_val_for_loader = create_dataset(
    tokenizer, dataset_val, max_length
)

# データローダの作成
dataloader_train = DataLoader(
    dataset_train_for_loader, batch_size=32, shuffle=True
)
dataloader_val = DataLoader(dataset_val_for_loader, batch_size=256)
# %%
# 8-16
# PyTorch Lightningのモデル
class BertForTokenClassification_pl(pl.LightningModule):
        
    def __init__(self, model_name, num_labels, lr):
        super().__init__()
        self.save_hyperparameters()
        self.bert_tc = BertForTokenClassification.from_pretrained(
            model_name,
            num_labels=num_labels
        )
        
    def training_step(self, batch, batch_idx):
        output = self.bert_tc(**batch)
        loss = output.loss
        self.log('train_loss', loss)
        return loss
        
    def validation_step(self, batch, batch_idx):
        output = self.bert_tc(**batch)
        val_loss = output.loss
        self.log('val_loss', val_loss)
        
    def configure_optimizers(self):
        return torch.optim.Adam(self.parameters(), lr=self.hparams.lr)

checkpoint = pl.callbacks.ModelCheckpoint(
    monitor='val_loss',
    mode='min',
    save_top_k=1,
    save_weights_only=True,
    dirpath='model/'
)

trainer = pl.Trainer(
    gpus=1,
    max_epochs=5,
    callbacks=[checkpoint]
)

# ファインチューニング
model = BertForTokenClassification_pl(
    MODEL_NAME, num_labels=9, lr=1e-5
)
trainer.fit(model, dataloader_train, dataloader_val)
best_model_path = checkpoint.best_model_path
# %%
