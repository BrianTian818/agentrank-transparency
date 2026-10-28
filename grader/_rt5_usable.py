# -*- coding: utf-8 -*-
"""「这段输出到底能不能判分」的唯一判定实现 —— 部署到 /opt/agentrank-rt5/_rt5_usable.py

被两处共用，保证「自测」与「社区投稿」的标准完全一致：
  1. grade_cli.py   —— 社区投稿入口。不可判分的直接拒收，绝不能记 0 分
  2. _rt5_filter.py —— 自测流水线。不可判分的标「未测」并写明原因，同样不记 0 分

原则：宁可标未测 / 拒收，也不冤枉产品。
      登录墙、注册墙、加载卡死是「产品没跑出来」，不是「产品跑得差」，
      把它记成 0 分是撒谎。

min_body 参数说明：
  自测用 120（抓的是整页，含侧栏，正文不到 120 字基本就是没跑出来）；
  社区投稿用 40（投稿人通常只粘答案区，没有侧栏，阈值要放宽，
  否则会误伤「答案确实就那么短」的正常投稿）。
"""
import re
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _rt5_norm import strip_cite      # noqa: E402


WALL = [
    "注册并重复您的请求", "请登录", "登录后", "登录以继续", "创建账号以查看结果",
    "未登录状态下", "Sign in to continue", "Log in to continue", "请稍后一分钟",
    "过多邮箱登录请求", "您的消息已保存", "手机端 秘塔",
]

REASON = {
    "注册并重复您的请求": "答案被锁在注册之后",
    "请登录": "需要登录才能查看结果",
    "登录后": "需要登录才能查看结果",
    "登录以继续": "需要登录才能查看结果",
    "创建账号以查看结果": "需要注册账号才能查看结果",
    "未登录状态下": "未登录且已超出试用次数",
    "Sign in to continue": "需要登录才能查看结果",
    "Log in to continue": "需要登录才能查看结果",
    "请稍后一分钟": "触发 IP 层面的登录限流",
    "过多邮箱登录请求": "触发 IP 层面的登录限流",
    "您的消息已保存": "需要登录才会真正执行",
}


def after_prompt(raw, prompt):
    """截掉最后一次出现的提示词回显，返回它之后的内容（正文）。

    用「去空白后的下标」定位再映射回原文，避免产品把空格吃掉导致匹配不上。
    """
    if not prompt:
        return raw
    tail = re.sub(r"\s+", "", prompt)[-40:]
    if not tail:
        return raw
    nr = re.sub(r"\s+", "", raw)
    idx = nr.rfind(tail)
    if idx < 0:
        return raw
    target = idx + len(tail)
    cnt = 0
    for j, ch in enumerate(raw):
        if not ch.isspace():
            cnt += 1
            if cnt == target:
                return raw[j + 1:]
    return raw


def judge(raw, prompt="", min_body=120):
    """返回 None 表示可以判分；返回字符串表示不可判分，字符串是写给投稿人看的原因。"""
    if not raw or not raw.strip():
        return "内容是空的"

    body = strip_cite(after_prompt(raw, prompt))

    # 页面没跑完：整页停留在这个状态上，抓到的只有 UI 骨架
    if "正在加载" in raw or "加载中" in body:
        return "页面长时间停留在「正在加载」，未产出结果"
    # 只剩 UI 骨架（侧栏 / 登录区），没有正文
    if "登录后继续搜索" in body and len(re.sub(r"\s+", "", body)) < 400:
        return "只剩下页面框架与登录区，未产出结果"

    if len(re.sub(r"\s+", "", body)) < min_body:
        for w in WALL:
            if w in raw:
                return REASON.get(w, "被登录/注册流程拦截")
        return "正文过短，无法判断是否来自一次真实运行"
    return None
