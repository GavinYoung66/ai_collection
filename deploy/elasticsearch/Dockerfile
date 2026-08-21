# 自定义 Elasticsearch 镜像：预装 analysis-ik 中文分词插件。
# 插件版本必须与 ES 版本严格一致（8.11.3），升级 ES 时需同步更换插件包。
FROM docker.elastic.co/elasticsearch/elasticsearch:8.11.3

# 构建期安装 ik 插件（infinilabs 官方镜像源），并校验安装结果
RUN bin/elasticsearch-plugin install --batch \
        https://release.infinilabs.com/analysis-ik/stable/elasticsearch-analysis-ik-8.11.3.zip \
    && bin/elasticsearch-plugin list | grep analysis-ik
