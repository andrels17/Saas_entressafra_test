
def calcular_kpis(df):

    total = len(df)

    concluidos = len(df[df["status"] == "concluido"])
    atrasados = len(df[df["status"] == "atrasado"])

    equipamentos = df["equipamento"].nunique()

    return {
        "total": total,
        "concluidos": concluidos,
        "atrasados": atrasados,
        "equipamentos": equipamentos
    }
