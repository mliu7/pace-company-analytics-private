from . import context as ctx_mod


def access(request):
    return {"acc": getattr(request, "acc", None) or ctx_mod.anonymous_context()}
