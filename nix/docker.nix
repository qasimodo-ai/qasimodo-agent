{
  perSystem =
    {
      config,
      pkgs,
      lib,
      ...
    }:
    {
      packages = {
        dockerImage = pkgs.dockerTools.streamLayeredImage {
          name = "qasimodo-agent";
          tag = "latest";
          contents = [ config.packages.qasimodo-agent ];
          config = {
            Entrypoint = [ (lib.getExe config.packages.qasimodo-agent) ];
          };
        };
      };
    };
}
