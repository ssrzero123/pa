# axiv_email
axiv论文爬虫</br>
1.爬虫：
    爬取axiv上的论文，包括文章标题，作者等信息，保持文章到本地</br>
2.信息提取
    从爬到的论文中提取邮箱，将提取的邮箱保存到CSV文件
###
<p>依赖</p> pip install pdfplumber</br>
<p>python>==3.6测试无问题</p> </br>
### 新增优化（去重 + 作者归属）
1. 不再把所有作者混在一起，改为每篇论文单独解析作者列表。  
2. 解析 PDF 中邮箱后，按规则归属到作者（无法匹配则记为 `UNKNOWN`）。  
3. 全局去重 `author + email`，避免同一作者邮箱重复出现。  
4. 同时输出纯邮箱去重结果，保留兼容性。  

### 使用方式
```bash
python3 axiv_email.py --count 20
```

### 输出文件
1. `author_email.csv`：`author,email,arxiv_id,title,subject`（作者邮箱归属 + 去重）  
2. `email.csv`：`email`（全局邮箱去重）  

同时希望大家提出宝贵意见，欢迎学习交流，如果你喜欢该项目，请收藏或者fork一下，你的主动将是我前行的动力</br>
###  
###
<p>如果有任何问题请联系我Email: lizhipengqilu@gmail.com</p>
<p>同时希望大家提出宝贵意见，欢迎学习交流，如果你喜欢该项目，请收藏或者fork一下，你的主动将是我前行的动力</p>
</br></br>
###
<img src="https://github.com/Frank-qlu/axiv-spyder/blob/master/images/1.png" />
<img src="https://github.com/Frank-qlu/axiv-spyder/blob/master/images/2.png" />
