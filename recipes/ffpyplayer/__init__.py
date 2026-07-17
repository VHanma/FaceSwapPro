"""FaceSwap Pro override for python-for-android's FFpyplayer recipe.

FFmpeg's encoder bundle is required for MP4 writing, but current FFmpeg builds no
longer provide libpostproc headers. FFpyplayer does not need post-processing for
MediaWriter, so force CONFIG_POSTPROC off while retaining all encoders/muxers.
"""

from os.path import join

from pythonforandroid.recipe import PyProjectRecipe, Recipe


class FaceSwapFFPyPlayerRecipe(PyProjectRecipe):
    version = "v4.5.1"
    url = "https://github.com/matham/ffpyplayer/archive/{version}.zip"
    depends = ["python3", "sdl2", "ffmpeg"]
    patches = ["setup.py.patch"]
    opt_depends = ["openssl", "ffpyplayer_codecs"]

    def get_recipe_env(self, arch, with_flags_in_cc=True):
        env = super().get_recipe_env(arch)

        ffmpeg_dir = Recipe.get_recipe("ffmpeg", self.ctx).get_build_dir(arch.arch)
        env["FFMPEG_INCLUDE_DIR"] = join(ffmpeg_dir, "include")
        env["FFMPEG_LIB_DIR"] = join(ffmpeg_dir, "lib")

        env["SDL_INCLUDE_DIR"] = join(
            self.ctx.bootstrap.build_dir, "jni", "SDL", "include"
        )
        env["SDL_LIB_DIR"] = join(self.ctx.bootstrap.build_dir, "libs", arch.arch)
        env["USE_SDL2_MIXER"] = "1"

        mixer_recipe = self.get_recipe("sdl2_mixer", self.ctx)
        env["SDL2_MIXER_INCLUDE_DIR"] = mixer_recipe.get_include_dirs(arch)[0]

        env["NDKPLATFORM"] = "NOTNONE"
        env["LIBLINK"] = "NOTNONE"

        # FFmpeg 8 no longer installs libpostproc/postprocess.h in this build.
        # MediaWriter uses libavcodec/libavformat and does not require postproc.
        env["CONFIG_POSTPROC"] = "0"
        return env


recipe = FaceSwapFFPyPlayerRecipe()
