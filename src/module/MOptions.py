# -*- coding: utf-8 -*-
#
import _pickle as cPickle
from module.MMath import MRect, MVector3D, MVector4D, MQuaternion, MMatrix4x4 # noqa
from utils.MLogger import MLogger # noqa

logger = MLogger(__name__, level=1)


class MOptions():

    def __init__(self, version_name, logging_level, data_set_list):
        self.version_name = version_name
        self.logging_level = logging_level
        self.data_set_list = data_set_list
    
    # 複数件のファイルセットの足IKの比率を再設定する
    def calc_leg_ratio(self):
        # まず一番小さいXZ比率と一番大きいXZ比率を取得する
        min_xz_ratio = 99999999999
        max_xz_ratio = -99999999999
        for data_set in self.data_set_list:
            if data_set.original_xz_ratio < min_xz_ratio:
                min_xz_ratio = data_set.original_xz_ratio
            
            if data_set.original_xz_ratio > max_xz_ratio:
                max_xz_ratio = data_set.original_xz_ratio
        
        # XZ比率の差(最大1.2とする)
        xz_ratio_diff = min(1.2, max_xz_ratio / min_xz_ratio)
        logger.test("xz_ratio_diff: %s", xz_ratio_diff)

        logger.info("")

        log_txt = "足の長さの比率 ---------\n"

        for n, data_set in enumerate(self.data_set_list):
            if min_xz_ratio == data_set.original_xz_ratio:
                # 比率が最小の場合、補正比率をかける
                data_set.xz_ratio = data_set.original_xz_ratio * xz_ratio_diff
            else:
                # 最小のXZ比率 × 補正比率 / 対象のXZ比率
                data_set.xz_ratio = (min_xz_ratio * xz_ratio_diff) / data_set.original_xz_ratio
            data_set.y_ratio = data_set.original_y_ratio

            log_txt = "{0}【No.{1}】　xz: {2}, y: {3} (元: xz: {4})\n".format(log_txt, (n + 1), data_set.xz_ratio, data_set.y_ratio, data_set.original_xz_ratio)

        logger.info(log_txt, decoration=MLogger.DECORATION_BOX)


class MOptionsDataSet():
    def __init__(self, motion_vmd_data, org_model_data, rep_model_data, output_vmd_path, substitute_model_flg, twist_flg):
        self.motion_vmd_data = motion_vmd_data
        self.org_model_data = org_model_data
        self.rep_model_data = rep_model_data
        self.output_vmd_path = output_vmd_path
        self.substitute_model_flg = substitute_model_flg
        self.twist_flg = twist_flg
        self.org_motion = cPickle.loads(cPickle.dumps(self.motion_vmd_data, -1))

        # 実際に計算に使う足IKの比率
        self.original_xz_ratio = 1
        self.original_y_ratio = 1

        # 足IKの比率
        self.xz_ratio = 1
        self.y_ratio = 1
